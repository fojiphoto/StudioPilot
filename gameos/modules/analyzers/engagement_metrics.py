"""Engagement metrics analyzer - rolls raw SDK events into GameMetricRecord.

Computes per game, per day (over a rolling window):
- DAU   : distinct devices with a session that day
- MAU   : distinct devices active in the trailing 30 days
- sessions, avg_playtime (from session_end durations)
- retention D1/D7/D30 : of the install-cohort N days before, % active on day N

This is the analytics half of our own "MMP-lite" - fully independent of GameAnalytics.
ARPDAU/ARPMAU are then derived in the dashboard (revenue / DAU or MAU).
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func

from gameos.kernel.models import Device, GameMetricRecord, SessionEvent
from gameos.kernel.module import Module, ModuleInfo, ModuleType
from gameos.kernel.runtime import Context

WINDOW_DAYS = 35  # recompute the recent window each cycle
RETENTION_DAYS = (1, 7, 30)


class EngagementMetrics(Module):
    info = ModuleInfo(
        name="engagement_metrics",
        type=ModuleType.ANALYZER,
        description="Rolls GameOS SDK events into DAU/MAU/retention/playtime per game.",
        default_interval_minutes=60,
    )

    def run(self, ctx: Context) -> None:
        end = date.today()
        start = end - timedelta(days=WINDOW_DAYS - 1)

        with ctx.session() as session:
            game_ids = [
                row[0]
                for row in session.query(SessionEvent.game_id)
                .filter(SessionEvent.date >= start - timedelta(days=30))
                .distinct().all()
            ]
            if not game_ids:
                self.log.debug("no SDK events yet - nothing to roll up")
                return

            total_rows = 0
            for game_id in game_ids:
                total_rows += self._compute_game(session, game_id, start, end)
            session.commit()

        self.log.info("rolled up engagement metrics for %d games (%d day-rows)", len(game_ids), total_rows)

    def _compute_game(self, session, game_id: int, start: date, end: date) -> int:
        # Active devices per day (distinct device_id with any session_* event).
        active_rows = (
            session.query(SessionEvent.date, SessionEvent.device_id)
            .filter(SessionEvent.game_id == game_id,
                    SessionEvent.date >= start - timedelta(days=30),
                    SessionEvent.event_type.in_(("session_start", "session_end")))
            .distinct().all()
        )
        active_by_day: dict[date, set[str]] = {}
        for day, device_id in active_rows:
            active_by_day.setdefault(day, set()).add(device_id)

        # Sessions + playtime per day.
        session_counts = dict(
            session.query(SessionEvent.date, func.count())
            .filter(SessionEvent.game_id == game_id, SessionEvent.event_type == "session_start",
                    SessionEvent.date >= start, SessionEvent.date <= end)
            .group_by(SessionEvent.date).all()
        )
        playtime = dict(
            session.query(SessionEvent.date, func.avg(SessionEvent.duration_sec))
            .filter(SessionEvent.game_id == game_id, SessionEvent.event_type == "session_end",
                    SessionEvent.date >= start, SessionEvent.date <= end)
            .group_by(SessionEvent.date).all()
        )

        # Install cohorts (device first_seen) for retention.
        cohort: dict[date, set[str]] = {}
        for device_id, first_seen in (
            session.query(Device.device_id, Device.first_seen)
            .filter(Device.game_id == game_id).all()
        ):
            cohort.setdefault(first_seen, set()).add(device_id)

        rows = 0
        day = start
        while day <= end:
            active_today = active_by_day.get(day, set())
            dau = len(active_today)
            mau = len({
                d for dd, devs in active_by_day.items()
                if day - timedelta(days=29) <= dd <= day for d in devs
            })
            retention = {}
            for n in RETENTION_DAYS:
                install_day = day - timedelta(days=n)
                base = cohort.get(install_day, set())
                retention[n] = (
                    round(len(base & active_today) / len(base), 4) if base else None
                )

            self._upsert(session, game_id, day, dau, mau,
                         int(session_counts.get(day, 0)),
                         float(playtime.get(day) or 0.0), retention)
            rows += 1
            day += timedelta(days=1)
        return rows

    def _upsert(self, session, game_id, day, dau, mau, sessions, avg_playtime, retention) -> None:
        row = (
            session.query(GameMetricRecord)
            .filter_by(game_id=game_id, date=day, country=None).one_or_none()
        )
        if row is None:
            row = GameMetricRecord(game_id=game_id, date=day, country=None)
            session.add(row)
        row.dau = dau
        row.mau = mau
        row.sessions = sessions
        row.avg_playtime = avg_playtime
        row.retention_d1 = retention.get(1)
        row.retention_d7 = retention.get(7)
        row.retention_d30 = retention.get(30)


def get_module() -> Module:
    return EngagementMetrics()
