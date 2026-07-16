"""AppLovin MAX connector - Revenue Reporting API.
Pulls day-level revenue/impressions/eCPM per app/country/network and stores
normalized AdRevenueRecord rows. Games are auto-registered on first sight.

Docs: https://developers.applovin.com/en/max/reporting-apis/revenue-reporting-api/
Auth: report key in APPLOVIN_REPORT_KEY (.env). Read-only key, no password involved.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import httpx

from gameos.kernel.models import AdRevenueRecord, Game
from gameos.kernel.module import Module, ModuleInfo, ModuleType
from gameos.kernel.runtime import Context

REPORT_URL = "https://r.applovin.com/maxReport"
COLUMNS = "day,application,package_name,platform,country,network,impressions,estimated_revenue,ecpm"
PULL_DAYS = 3  # rolling window; MAX revises recent days, so re-pull and replace

_PLATFORM_MAP = {"fireos": "amazon", "fire_os": "amazon"}


class AppLovinMax(Module):
    info = ModuleInfo(
        name="applovin_max",
        type=ModuleType.CONNECTOR,
        description="AppLovin MAX ad revenue (impressions/revenue/eCPM by app/country/network).",
        default_interval_minutes=60,
    )

    def _fetch(self, start: date, end: date) -> list[dict]:
        key = os.getenv("APPLOVIN_REPORT_KEY", "")
        if not key:
            raise RuntimeError("APPLOVIN_REPORT_KEY missing from .env")
        response = httpx.get(
            REPORT_URL,
            params={
                "api_key": key,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "columns": COLUMNS,
                "format": "json",
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code", 200) != 200:
            raise RuntimeError(f"MAX report error: {payload}")
        return payload.get("results", [])

    def _game_id(self, ctx: Context, cache: dict, name: str, store: str, package: str | None) -> int:
        cache_key = (name, store)
        if cache_key in cache:
            return cache[cache_key]
        with ctx.session() as session:
            game = session.query(Game).filter_by(name=name, store=store).one_or_none()
            if game is None:
                game = Game(name=name, store=store, package_name=package)
                session.add(game)
                session.commit()
                self.log.info("registered new game: %s (%s)", name, store)
            cache[cache_key] = game.id
        return cache[cache_key]

    def run(self, ctx: Context) -> None:
        end = date.today()
        self._ingest(ctx, end - timedelta(days=PULL_DAYS - 1), end)

    def backfill(self, ctx: Context, days: int) -> str:
        days = min(days, 45)  # MAX report API window limit
        end = date.today()
        start = end - timedelta(days=days - 1)
        count = self._ingest(ctx, start, end)
        return f"backfilled {count} rows over {days} days ({start}..{end})"

    def _ingest(self, ctx: Context, start: date, end: date) -> int:
        rows = self._fetch(start, end)
        self.log.info("MAX returned %d rows for %s..%s", len(rows), start, end)

        game_cache: dict = {}
        records = []
        for row in rows:
            platform = row.get("platform", "").lower()
            store = _PLATFORM_MAP.get(platform, platform) or "unknown"
            game_id = self._game_id(
                ctx, game_cache, row.get("application") or "unknown", store, row.get("package_name")
            )
            records.append(
                AdRevenueRecord(
                    game_id=game_id,
                    date=date.fromisoformat(row["day"]),
                    source="applovin_max",
                    network=row.get("network") or "unknown",
                    country=(row.get("country") or None),
                    platform=store,
                    impressions=int(row.get("impressions") or 0),
                    ecpm=float(row.get("ecpm") or 0.0),
                    revenue=float(row.get("estimated_revenue") or 0.0),
                )
            )

        # Idempotent replace: MAX revises recent days, so wipe the window and rewrite it.
        with ctx.session() as session:
            session.query(AdRevenueRecord).filter(
                AdRevenueRecord.source == "applovin_max",
                AdRevenueRecord.date >= start,
                AdRevenueRecord.date <= end,
            ).delete()
            session.add_all(records)
            session.commit()

        ctx.mark_synced("applovin_max", freshness_note="near real-time")
        self.log.info("stored %d AdRevenueRecord rows", len(records))
        return len(records)

    def self_test(self, ctx: Context) -> tuple[bool, str]:
        try:
            end = date.today()
            rows = self._fetch(end - timedelta(days=2), end)
        except Exception as exc:
            return False, f"API call failed: {exc}"
        revenue = sum(float(r.get("estimated_revenue") or 0) for r in rows)
        apps = {r.get("application") for r in rows}
        return True, f"{len(rows)} rows, {len(apps)} apps, ${revenue:.2f} revenue (last 3 days)"


def get_module() -> Module:
    return AppLovinMax()
