"""Meta (Facebook) Ads connector - Marketing API Insights.
Pulls day-level campaign spend/installs/CPI/clicks/impressions and stores
normalized CampaignRecord rows (ua_platform="meta").

Docs: https://developers.facebook.com/docs/marketing-api/insights
Auth: user access token in META_ACCESS_TOKEN (.env). Accounts to pull are in
META_AD_ACCOUNT_IDS (comma-separated, e.g. "act_123,act_456").
Campaign->game mapping happens later (game_id stays NULL until mapped).
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

import httpx

from gameos.kernel.models import CampaignMap, CampaignRecord
from gameos.kernel.module import Module, ModuleInfo, ModuleType
from gameos.kernel.runtime import Context

API_BASE = "https://graph.facebook.com/v19.0"
PULL_DAYS = 3
_INSTALL_ACTIONS = {"mobile_app_install", "omni_app_install", "app_install"}


class MetaAds(Module):
    info = ModuleInfo(
        name="meta_ads",
        type=ModuleType.CONNECTOR,
        description="Meta UA campaign spend/installs/CPI (Ads Insights API).",
        default_interval_minutes=60,
    )

    def _accounts(self) -> list[str]:
        raw = os.getenv("META_AD_ACCOUNT_IDS", "").strip()
        if not raw:
            raise RuntimeError("META_AD_ACCOUNT_IDS missing from .env")
        return [a.strip() for a in raw.split(",") if a.strip()]

    def _fetch_account(self, account: str, start: date, end: date) -> list[dict]:
        token = os.getenv("META_ACCESS_TOKEN", "")
        if not token:
            raise RuntimeError("META_ACCESS_TOKEN missing from .env")
        rows: list[dict] = []
        url = f"{API_BASE}/{account}/insights"
        params = {
            "access_token": token,
            "level": "campaign",
            "fields": "campaign_id,campaign_name,spend,impressions,clicks,actions",
            "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()}),
            "time_increment": 1,
            "limit": 500,
        }
        while url:
            response = httpx.get(url, params=params, timeout=60)
            response.raise_for_status()
            payload = response.json()
            rows.extend(payload.get("data", []))
            url = payload.get("paging", {}).get("next")
            params = None  # `next` URL already carries everything
        return rows

    @staticmethod
    def _installs(row: dict) -> int:
        for action in row.get("actions") or []:
            if action.get("action_type") in _INSTALL_ACTIONS:
                return int(float(action.get("value") or 0))
        return 0

    def run(self, ctx: Context) -> None:
        end = date.today()
        start = end - timedelta(days=PULL_DAYS - 1)

        with ctx.session() as session:
            mapping = dict(
                session.query(CampaignMap.campaign_id, CampaignMap.game_id)
                .filter(CampaignMap.ua_platform == "meta")
                .all()
            )
        records = []
        for account in self._accounts():
            rows = self._fetch_account(account, start, end)
            self.log.info("meta %s returned %d campaign-day rows", account, len(rows))
            for row in rows:
                spend = float(row.get("spend") or 0.0)
                installs = self._installs(row)
                campaign_id = str(row.get("campaign_id"))
                records.append(
                    CampaignRecord(
                        game_id=mapping.get(campaign_id),
                        date=date.fromisoformat(row["date_start"]),
                        ua_platform="meta",
                        campaign_id=campaign_id,
                        campaign_name=row.get("campaign_name"),
                        spend=spend,
                        installs=installs,
                        cpi=(spend / installs) if installs else 0.0,
                        clicks=int(row.get("clicks") or 0),
                        impressions=int(row.get("impressions") or 0),
                    )
                )

        with ctx.session() as session:
            session.query(CampaignRecord).filter(
                CampaignRecord.ua_platform == "meta",
                CampaignRecord.date >= start,
                CampaignRecord.date <= end,
            ).delete()
            session.add_all(records)
            session.commit()

        ctx.mark_synced("meta_ads", freshness_note="near real-time")
        self.log.info("stored %d CampaignRecord rows", len(records))

    def self_test(self, ctx: Context) -> tuple[bool, str]:
        try:
            end = date.today()
            start = end - timedelta(days=2)
            total_rows = 0
            total_spend = 0.0
            for account in self._accounts():
                rows = self._fetch_account(account, start, end)
                total_rows += len(rows)
                total_spend += sum(float(r.get("spend") or 0) for r in rows)
        except Exception as exc:
            return False, f"API call failed: {exc}"
        return True, f"{total_rows} campaign-day rows, ${total_spend:.2f} spend (last 3 days)"


def get_module() -> Module:
    return MetaAds()
