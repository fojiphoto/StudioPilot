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

from gameos.kernel.models import CampaignMap, CampaignRecord, Game
from gameos.kernel.module import Module, ModuleInfo, ModuleType
from gameos.kernel.runtime import Context

REPORT_URL = "https://ss-api.mintegral.com/api/v2/reports/data"
CAMPAIGN_URL = "https://ss-api.mintegral.com/api/open/v1/campaign"
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

    def _fetch_campaigns(self) -> list[dict]:
        """Campaign list from the open API - gives campaign_name and bundle_id."""
        campaigns: list[dict] = []
        page = 1
        while True:
            response = httpx.get(
                CAMPAIGN_URL, headers=self._headers(), params={"page": page, "limit": 50}, timeout=60
            )
            response.raise_for_status()
            data = response.json().get("data") or {}
            batch = data.get("list") or []
            campaigns.extend(batch)
            if len(campaigns) >= int(data.get("total") or 0) or not batch:
                break
            page += 1
        return campaigns

    def _sync_campaign_map(self, ctx: Context) -> dict[str, str]:
        """Auto-map campaigns to games via bundle_id == Game.package_name.
        Owner overrides via `gameos map` always win (existing entries untouched).
        Returns {campaign_id: campaign_name} for name enrichment."""
        try:
            campaigns = self._fetch_campaigns()
        except Exception:
            self.log.exception("campaign list fetch failed - skipping auto-map this run")
            return {}
        names: dict[str, str] = {}
        with ctx.session() as session:
            mapped = {
                m.campaign_id
                for m in session.query(CampaignMap).filter_by(ua_platform="mintegral").all()
            }
            games_by_package = {}
            games_by_id = {}
            for game in session.query(Game).filter(Game.package_name.is_not(None)).all():
                games_by_package.setdefault(game.package_name.lower(), game.id)
                games_by_id[game.id] = game
            enriched = 0
            for campaign in campaigns:
                campaign_id = str(campaign.get("campaign_id") or "")
                if not campaign_id:
                    continue
                names[campaign_id] = campaign.get("campaign_name") or ""
                # Enrich human display name: bundle_id -> product_name (real title).
                bundle_l = (campaign.get("bundle_id") or "").lower()
                product = (campaign.get("product_name") or "").strip()
                gid = games_by_package.get(bundle_l)
                if product and gid and not games_by_id[gid].display_name:
                    games_by_id[gid].display_name = product
                    enriched += 1
                if campaign_id in mapped:
                    continue
                bundle = (campaign.get("bundle_id") or "").lower()
                game_id = games_by_package.get(bundle)
                if game_id is None:
                    self.log.info(
                        "campaign %s (%s) bundle %s has no matching game yet - map manually with "
                        "`gameos map mintegral %s <game_id>`",
                        campaign_id, names[campaign_id], bundle or "?", campaign_id,
                    )
                    continue
                session.add(
                    CampaignMap(ua_platform="mintegral", campaign_id=campaign_id, game_id=game_id)
                )
                self.log.info("auto-mapped campaign %s (%s) -> game #%s [%s]",
                              campaign_id, names[campaign_id], game_id, bundle)
            if enriched:
                self.log.info("enriched %d game display names from Mintegral product_name", enriched)
            session.commit()
        return names

    def run(self, ctx: Context) -> None:
        names = self._sync_campaign_map(ctx)
        end = date.today() - timedelta(days=1)  # D-1: today's data doesn't exist yet
        self._ingest(ctx, end - timedelta(days=PULL_DAYS - 1), end, names)

    def backfill(self, ctx: Context, days: int) -> str:
        """API allows 7 days per request - walk backwards in 7-day chunks."""
        names = self._sync_campaign_map(ctx)
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=days - 1)
        total = 0
        chunk_end = end
        while chunk_end >= start:
            chunk_start = max(start, chunk_end - timedelta(days=6))
            total += self._ingest(ctx, chunk_start, chunk_end, names)
            chunk_end = chunk_start - timedelta(days=1)
        return f"backfilled {total} rows over {days} days ({start}..{end})"

    def _ingest(self, ctx: Context, start: date, end: date, names: dict[str, str] | None = None) -> int:
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
                    campaign_name=(names or {}).get(campaign_id),
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
