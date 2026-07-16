"""Google Ads connector - GoogleAdsService.searchStream (REST) with GAQL.
Pulls day-level campaign spend/installs/clicks/impressions and stores normalized
CampaignRecord rows (ua_platform="google").

Auth: OAuth refresh token (scripts/google_oauth.py googleads) + developer token.
NOTE: until the developer token has Basic Access approval, production-account
queries fail with DEVELOPER_TOKEN_NOT_APPROVED - the self-test explains this.

GOOGLE_ADS_LOGIN_CUSTOMER_ID (optional): set to the MCC id (digits only) if the
queried account is only reachable through the manager account.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

import httpx

from gameos.kernel.models import CampaignRecord
from gameos.kernel.module import Module, ModuleInfo, ModuleType
from gameos.kernel.runtime import Context

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_HOST = "https://googleads.googleapis.com"
# Google Ads API versions rotate every ~4 months; try newest first.
VERSION_CANDIDATES = ["v21", "v20", "v19", "v18"]
PULL_DAYS = 3

QUERY = """
SELECT segments.date, campaign.id, campaign.name,
       metrics.cost_micros, metrics.clicks, metrics.impressions,
       metrics.conversions
FROM campaign
WHERE segments.date BETWEEN '{start}' AND '{end}'
"""


class GoogleAds(Module):
    info = ModuleInfo(
        name="google_ads",
        type=ModuleType.CONNECTOR,
        description="Google Ads UA campaign spend/installs/CPI (GAQL searchStream).",
        default_interval_minutes=60,
    )

    def __init__(self) -> None:
        super().__init__()
        self._version: str | None = None

    def _access_token(self) -> str:
        client_id = os.getenv("GOOGLE_ADS_CLIENT_ID", "")
        client_secret = os.getenv("GOOGLE_ADS_CLIENT_SECRET", "")
        refresh_token = os.getenv("GOOGLE_ADS_REFRESH_TOKEN", "")
        if not (client_id and client_secret and refresh_token):
            raise RuntimeError("GOOGLE_ADS_CLIENT_ID / _SECRET / _REFRESH_TOKEN missing from .env")
        response = httpx.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def _headers(self) -> dict:
        developer_token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
        if not developer_token:
            raise RuntimeError("GOOGLE_ADS_DEVELOPER_TOKEN missing from .env")
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "developer-token": developer_token,
        }
        login_cid = os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").replace("-", "").strip()
        if login_cid:
            headers["login-customer-id"] = login_cid
        return headers

    def _customer_id(self) -> str:
        cid = os.getenv("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", "").strip()
        if not cid:
            raise RuntimeError("GOOGLE_ADS_CUSTOMER_ID missing from .env")
        return cid

    def _fetch(self, start: date, end: date) -> list[dict]:
        headers = self._headers()
        cid = self._customer_id()
        query = QUERY.format(start=start.isoformat(), end=end.isoformat())
        versions = [self._version] if self._version else VERSION_CANDIDATES
        last_error = None
        for version in versions:
            response = httpx.post(
                f"{API_HOST}/{version}/customers/{cid}/googleAds:searchStream",
                headers=headers,
                json={"query": query},
                timeout=120,
            )
            if response.status_code == 404:
                last_error = f"{version} not available"
                continue  # try older API version
            if response.status_code != 200:
                raise RuntimeError(f"Google Ads API {response.status_code}: {response.text[:400]}")
            self._version = version
            results = []
            for chunk in response.json():
                results.extend(chunk.get("results", []))
            return results
        raise RuntimeError(f"no working Google Ads API version found ({last_error})")

    def run(self, ctx: Context) -> None:
        end = date.today()
        start = end - timedelta(days=PULL_DAYS - 1)
        rows = self._fetch(start, end)
        self.log.info("google ads returned %d campaign-day rows for %s..%s", len(rows), start, end)

        records = []
        for row in rows:
            campaign = row.get("campaign", {})
            metrics = row.get("metrics", {})
            segments = row.get("segments", {})
            spend = float(metrics.get("costMicros") or 0) / 1_000_000
            installs = int(float(metrics.get("conversions") or 0))
            records.append(
                CampaignRecord(
                    game_id=None,
                    date=date.fromisoformat(segments["date"]),
                    ua_platform="google",
                    campaign_id=str(campaign.get("id")),
                    campaign_name=campaign.get("name"),
                    spend=spend,
                    installs=installs,
                    cpi=(spend / installs) if installs else 0.0,
                    clicks=int(metrics.get("clicks") or 0),
                    impressions=int(metrics.get("impressions") or 0),
                )
            )

        with ctx.session() as session:
            session.query(CampaignRecord).filter(
                CampaignRecord.ua_platform == "google",
                CampaignRecord.date >= start,
                CampaignRecord.date <= end,
            ).delete()
            session.add_all(records)
            session.commit()

        ctx.mark_synced("google_ads", freshness_note="near real-time")
        self.log.info("stored %d CampaignRecord rows", len(records))

    def self_test(self, ctx: Context) -> tuple[bool, str]:
        try:
            end = date.today()
            rows = self._fetch(end - timedelta(days=2), end)
        except Exception as exc:
            message = str(exc)
            if "DEVELOPER_TOKEN_NOT_APPROVED" in message or "NOT_ADS_USER" in message:
                return False, (
                    "developer token not approved for production yet - EXPECTED until "
                    "Google approves the Basic Access application. Everything else is wired."
                )
            return False, f"API call failed: {message[:300]}"
        spend = sum(float(r.get("metrics", {}).get("costMicros") or 0) for r in rows) / 1_000_000
        return True, f"{len(rows)} campaign-day rows, ${spend:.2f} spend (last 3 days)"


def get_module() -> Module:
    return GoogleAds()
