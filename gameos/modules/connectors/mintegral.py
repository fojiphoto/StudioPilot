"""Mintegral connector - advertiser Reporting API v2.
Pulls day-level campaign spend/installs/clicks/impressions and stores normalized
CampaignRecord rows (ua_platform="mintegral").

IMPORTANT: Mintegral data is D-1 (lands ~1.5h after day end) - never treat as real-time.

API quirks (all verified live):
- Auth headers: access-key, timestamp, token = md5(api_key + md5(timestamp))
- Async: first GET (no type) returns code 201 "generating"; poll until code 200,
  then GET with type=2 returns the data as TSV text.
- Max 7 days per request.
"""
from __future__ import annotations

import hashlib
import os
import time
from datetime import date, timedelta

import httpx

from gameos.kernel.models import CampaignMap, CampaignRecord
from gameos.kernel.module import Module, ModuleInfo, ModuleType
from gameos.kernel.runtime import Context

REPORT_URL = "https://ss-api.mintegral.com/api/v2/reports/data"
PULL_DAYS = 7  # also the API's per-request maximum
POLL_ATTEMPTS = 30
POLL_SLEEP_SECONDS = 10
PER_PAGE = 1000


def _md5(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()


class Mintegral(Module):
    info = ModuleInfo(
        name="mintegral",
        type=ModuleType.CONNECTOR,
        description="Mintegral UA campaign spend/installs (Reporting API, D-1 freshness).",
        default_interval_minutes=360,  # data is D-1; no point hammering it
    )

    def _headers(self) -> dict:
        access_key = os.getenv("MINTEGRAL_ACCESS_KEY", "")
        api_key = os.getenv("MINTEGRAL_API_KEY", "")
        if not (access_key and api_key):
            raise RuntimeError("MINTEGRAL_ACCESS_KEY / MINTEGRAL_API_KEY missing from .env")
        timestamp = str(int(time.time()))
        return {
            "access-key": access_key,
            "timestamp": timestamp,
            "token": _md5(api_key + _md5(timestamp)),
        }

    def _base_params(self, start: date, end: date) -> dict:
        return {
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "utc_offset": 0,
            "time_granularity": "daily",
            "dimension_option": "Campaign",
        }

    def _fetch(self, start: date, end: date) -> list[dict]:
        params = self._base_params(start, end)
        # Step 1: trigger generation and poll until ready (fresh token per attempt).
        ready = False
        for _ in range(POLL_ATTEMPTS):
            response = httpx.get(REPORT_URL, headers=self._headers(), params=params, timeout=60)
            response.raise_for_status()
            body = response.text
            if '"code":200' in body:
                ready = True
                break
            if '"code":201' not in body and '"code":202' not in body:
                raise RuntimeError(f"Mintegral report error: {body[:300]}")
            time.sleep(POLL_SLEEP_SECONDS)
        if not ready:
            raise RuntimeError("Mintegral report still generating after poll timeout")

        # Step 2: fetch TSV pages with type=2.
        rows: list[dict] = []
        page = 1
        while True:
            response = httpx.get(
                REPORT_URL,
                headers=self._headers(),
                params={**params, "type": 2, "page": page, "per_page": PER_PAGE},
                timeout=120,
            )
            response.raise_for_status()
            lines = [line for line in response.text.strip().splitlines() if line.strip()]
            if not lines or lines[0].startswith('{"code"'):
                break
            header = [h.strip().lower().replace(" ", "_") for h in lines[0].split("\t")]
            page_rows = [dict(zip(header, line.split("\t"))) for line in lines[1:]]
            rows.extend(page_rows)
            if len(page_rows) < PER_PAGE:
                break
            page += 1
        return rows

    def run(self, ctx: Context) -> None:
        end = date.today() - timedelta(days=1)  # D-1: today's data doesn't exist yet
        self._ingest(ctx, end - timedelta(days=PULL_DAYS - 1), end)

    def backfill(self, ctx: Context, days: int) -> str:
        """API allows 7 days per request - walk backwards in 7-day chunks."""
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=days - 1)
        total = 0
        chunk_end = end
        while chunk_end >= start:
            chunk_start = max(start, chunk_end - timedelta(days=6))
            total += self._ingest(ctx, chunk_start, chunk_end)
            chunk_end = chunk_start - timedelta(days=1)
        return f"backfilled {total} rows over {days} days ({start}..{end})"

    def _ingest(self, ctx: Context, start: date, end: date) -> int:
        rows = self._fetch(start, end)
        self.log.info("mintegral returned %d campaign-day rows for %s..%s", len(rows), start, end)

        with ctx.session() as session:
            mapping = dict(
                session.query(CampaignMap.campaign_id, CampaignMap.game_id)
                .filter(CampaignMap.ua_platform == "mintegral")
                .all()
            )
        records = []
        for row in rows:
            raw_date = row.get("date", "")
            if len(raw_date) != 8:
                continue
            spend = float(row.get("spend") or 0.0)
            installs = int(float(row.get("conversion") or 0))
            campaign_id = str(row.get("campaign_id", ""))
            records.append(
                CampaignRecord(
                    game_id=mapping.get(campaign_id),
                    date=date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:8])),
                    ua_platform="mintegral",
                    campaign_id=campaign_id,
                    campaign_name=None,  # Campaign dimension returns ids only
                    spend=spend,
                    installs=installs,
                    cpi=(spend / installs) if installs else 0.0,
                    clicks=int(float(row.get("click") or 0)),
                    impressions=int(float(row.get("impression") or 0)),
                )
            )

        with ctx.session() as session:
            session.query(CampaignRecord).filter(
                CampaignRecord.ua_platform == "mintegral",
                CampaignRecord.date >= start,
                CampaignRecord.date <= end,
            ).delete()
            session.add_all(records)
            session.commit()

        ctx.mark_synced("mintegral", freshness_note="D-1 (lands ~1.5h after day end)")
        self.log.info("stored %d CampaignRecord rows", len(records))
        return len(records)

    def self_test(self, ctx: Context) -> tuple[bool, str]:
        try:
            end = date.today() - timedelta(days=1)
            rows = self._fetch(end - timedelta(days=PULL_DAYS - 1), end)
        except Exception as exc:
            return False, f"API call failed: {exc}"
        spend = sum(float(r.get("spend") or 0) for r in rows)
        campaigns = {r.get("campaign_id") for r in rows}
        return True, f"{len(rows)} rows, {len(campaigns)} campaigns, ${spend:.2f} spend (last 7 days, D-1)"


def get_module() -> Module:
    return Mintegral()
