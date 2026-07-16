"""AdMob connector - Mediation Report API.
Pulls day-level earnings/impressions/eCPM per app/country/ad-source and stores
normalized AdRevenueRecord rows (source="admob"). Games auto-register on first sight.

Docs: https://developers.google.com/admob/api/v1/mediation-report
Auth: OAuth refresh token in .env (obtained via scripts/google_oauth.py admob).
Publisher ID: taken from ADMOB_PUBLISHER_ID if set, otherwise auto-detected
via GET /v1/accounts on first run.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import httpx

from gameos.kernel.models import AdRevenueRecord, Game
from gameos.kernel.module import Module, ModuleInfo, ModuleType
from gameos.kernel.runtime import Context

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://admob.googleapis.com/v1"
PULL_DAYS = 3  # rolling window, same idempotent-replace strategy as applovin_max

_MICROS = 1_000_000


class AdMob(Module):
    info = ModuleInfo(
        name="admob",
        type=ModuleType.CONNECTOR,
        description="AdMob mediation revenue (earnings/impressions/eCPM by app/country/ad source).",
        default_interval_minutes=60,
    )

    def __init__(self) -> None:
        super().__init__()
        self._publisher_id: str | None = None

    def _access_token(self) -> str:
        client_id = os.getenv("ADMOB_CLIENT_ID", "")
        client_secret = os.getenv("ADMOB_CLIENT_SECRET", "")
        refresh_token = os.getenv("ADMOB_REFRESH_TOKEN", "")
        if not (client_id and client_secret and refresh_token):
            raise RuntimeError("ADMOB_CLIENT_ID / ADMOB_CLIENT_SECRET / ADMOB_REFRESH_TOKEN missing from .env")
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

    def _publisher(self, headers: dict) -> str:
        if self._publisher_id:
            return self._publisher_id
        configured = os.getenv("ADMOB_PUBLISHER_ID", "").strip()
        if configured:
            self._publisher_id = configured
            return configured
        response = httpx.get(f"{API_BASE}/accounts", headers=headers, timeout=30)
        response.raise_for_status()
        accounts = response.json().get("account", [])
        if not accounts:
            raise RuntimeError("no AdMob accounts visible for this login")
        self._publisher_id = accounts[0]["publisherId"]
        self.log.info("auto-detected AdMob publisher: %s", self._publisher_id)
        return self._publisher_id

    def _fetch(self, start: date, end: date) -> list[dict]:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        publisher = self._publisher(headers)
        spec = {
            "reportSpec": {
                "dateRange": {
                    "startDate": {"year": start.year, "month": start.month, "day": start.day},
                    "endDate": {"year": end.year, "month": end.month, "day": end.day},
                },
                "dimensions": ["DATE", "APP", "PLATFORM", "COUNTRY", "AD_SOURCE"],
                "metrics": ["ESTIMATED_EARNINGS", "IMPRESSIONS", "OBSERVED_ECPM"],
            }
        }
        response = httpx.post(
            f"{API_BASE}/accounts/{publisher}/mediationReport:generate",
            headers=headers,
            json=spec,
            timeout=120,
        )
        response.raise_for_status()
        # Response is a JSON array of chunks: header, then {"row": {...}} entries, then footer.
        rows = []
        for chunk in response.json():
            if "row" in chunk:
                rows.append(chunk["row"])
        return rows

    @staticmethod
    def _dim(row: dict, name: str, label: bool = False) -> str:
        value = row.get("dimensionValues", {}).get(name, {})
        return (value.get("displayLabel") if label else value.get("value")) or ""

    @staticmethod
    def _metric(row: dict, name: str) -> float:
        value = row.get("metricValues", {}).get(name, {})
        if "microsValue" in value:
            return float(value["microsValue"]) / _MICROS
        if "integerValue" in value:
            return float(value["integerValue"])
        return float(value.get("doubleValue") or 0.0)

    def _game_id(self, ctx: Context, cache: dict, name: str, store: str) -> int:
        cache_key = (name, store)
        if cache_key in cache:
            return cache[cache_key]
        with ctx.session() as session:
            game = session.query(Game).filter_by(name=name, store=store).one_or_none()
            if game is None:
                game = Game(name=name, store=store)
                session.add(game)
                session.commit()
                self.log.info("registered new game: %s (%s)", name, store)
            cache[cache_key] = game.id
        return cache[cache_key]

    def run(self, ctx: Context) -> None:
        end = date.today()
        self._ingest(ctx, end - timedelta(days=PULL_DAYS - 1), end)

    def backfill(self, ctx: Context, days: int) -> str:
        end = date.today()
        start = end - timedelta(days=days - 1)
        count = self._ingest(ctx, start, end)
        return f"backfilled {count} rows over {days} days ({start}..{end})"

    def _ingest(self, ctx: Context, start: date, end: date) -> int:
        rows = self._fetch(start, end)
        self.log.info("AdMob returned %d rows for %s..%s", len(rows), start, end)

        game_cache: dict = {}
        records = []
        for row in rows:
            raw_date = self._dim(row, "DATE")  # "20260713"
            if len(raw_date) != 8:
                continue
            store = self._dim(row, "PLATFORM").lower() or "unknown"  # iOS / Android
            app_name = self._dim(row, "APP", label=True) or self._dim(row, "APP") or "unknown"
            game_id = self._game_id(ctx, game_cache, app_name, store)
            records.append(
                AdRevenueRecord(
                    game_id=game_id,
                    date=date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:8])),
                    source="admob",
                    network=self._dim(row, "AD_SOURCE", label=True) or "unknown",
                    country=self._dim(row, "COUNTRY") or None,
                    platform=store,
                    impressions=int(self._metric(row, "IMPRESSIONS")),
                    ecpm=self._metric(row, "OBSERVED_ECPM"),
                    revenue=self._metric(row, "ESTIMATED_EARNINGS"),
                )
            )

        with ctx.session() as session:
            session.query(AdRevenueRecord).filter(
                AdRevenueRecord.source == "admob",
                AdRevenueRecord.date >= start,
                AdRevenueRecord.date <= end,
            ).delete()
            session.add_all(records)
            session.commit()

        ctx.mark_synced("admob", freshness_note="near real-time")
        self.log.info("stored %d AdRevenueRecord rows", len(records))
        return len(records)

    def self_test(self, ctx: Context) -> tuple[bool, str]:
        try:
            end = date.today()
            rows = self._fetch(end - timedelta(days=2), end)
        except Exception as exc:
            return False, f"API call failed: {exc}"
        revenue = sum(self._metric(r, "ESTIMATED_EARNINGS") for r in rows)
        apps = {self._dim(r, "APP", label=True) for r in rows}
        return True, f"{len(rows)} rows, {len(apps)} apps, ${revenue:.2f} revenue (last 3 days)"


def get_module() -> Module:
    return AdMob()
