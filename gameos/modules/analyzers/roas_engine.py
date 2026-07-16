"""ROAS engine - the core question: is the money we spend coming back?

Computes revenue vs spend over a rolling window (portfolio-wide, plus per-game where
campaigns are mapped to games) and raises alerts:
- portfolio ROAS negative while money is being spent
- any mapped game with meaningful spend and ROAS < 1

Campaigns with game_id NULL are aggregated as "unmapped spend" - still counted in
portfolio ROAS (money is money) but flagged so the owner maps them.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func

from gameos.kernel.models import AdRevenueRecord, Alert, CampaignRecord, Game
from gameos.kernel.module import Module, ModuleInfo, ModuleType
from gameos.kernel.runtime import Context

WINDOW_DAYS = 7
MIN_GAME_SPEND_FOR_ALERT = 5.0  # don't alarm over pocket change
ALERT_COOLDOWN_HOURS = 24


class RoasEngine(Module):
    info = ModuleInfo(
        name="roas_engine",
        type=ModuleType.ANALYZER,
        description=f"Portfolio & per-game ROAS over the last {WINDOW_DAYS} days; negative-ROAS alerts.",
        default_interval_minutes=60,
    )

    def _alert(self, ctx: Context, severity: str, message: str, game_id: int | None = None) -> None:
        """Insert an alert unless the same message fired within the cooldown window."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ALERT_COOLDOWN_HOURS)
        with ctx.session() as session:
            recent = (
                session.query(Alert)
                .filter(Alert.module == self.info.name, Alert.message == message, Alert.created_at >= cutoff)
                .first()
            )
            if recent:
                return
            session.add(Alert(severity=severity, module=self.info.name, game_id=game_id, message=message))
            session.commit()
        self.log.warning("ALERT [%s] %s", severity, message)

    def run(self, ctx: Context) -> None:
        since = date.today() - timedelta(days=WINDOW_DAYS - 1)
        with ctx.session() as session:
            revenue = (
                session.query(func.coalesce(func.sum(AdRevenueRecord.revenue), 0.0))
                .filter(AdRevenueRecord.date >= since)
                .scalar()
            )
            spend = (
                session.query(func.coalesce(func.sum(CampaignRecord.spend), 0.0))
                .filter(CampaignRecord.date >= since)
                .scalar()
            )
            unmapped_spend = (
                session.query(func.coalesce(func.sum(CampaignRecord.spend), 0.0))
                .filter(CampaignRecord.date >= since, CampaignRecord.game_id.is_(None))
                .scalar()
            )
            per_game_spend = dict(
                session.query(CampaignRecord.game_id, func.sum(CampaignRecord.spend))
                .filter(CampaignRecord.date >= since, CampaignRecord.game_id.is_not(None))
                .group_by(CampaignRecord.game_id)
                .all()
            )
            per_game_revenue = dict(
                session.query(AdRevenueRecord.game_id, func.sum(AdRevenueRecord.revenue))
                .filter(AdRevenueRecord.date >= since)
                .group_by(AdRevenueRecord.game_id)
                .all()
            )
            game_names = dict(session.query(Game.id, Game.name).all())

        roas = (revenue / spend) if spend else None
        summary = {
            "window_days": WINDOW_DAYS,
            "revenue": round(revenue, 2),
            "spend": round(spend, 2),
            "roas": round(roas, 2) if roas is not None else None,
            "unmapped_spend": round(unmapped_spend, 2),
        }
        ctx.cycle_state["roas"] = summary
        self.log.info(
            "last %dd: revenue $%.2f, spend $%.2f, ROAS %s",
            WINDOW_DAYS, revenue, spend, f"{roas:.2f}" if roas is not None else "n/a (no spend)",
        )

        if spend > 0 and roas is not None and roas < 1.0:
            self._alert(
                ctx, "warn",
                f"Portfolio ROAS negative: ${revenue:.2f} revenue vs ${spend:.2f} spend "
                f"(ROAS {roas:.2f}) over last {WINDOW_DAYS}d",
            )
        if unmapped_spend > 0:
            self._alert(
                ctx, "info",
                f"${unmapped_spend:.2f} spend (last {WINDOW_DAYS}d) is not mapped to any game - "
                f"map campaigns to games for per-game ROAS",
            )
        for game_id, game_spend in per_game_spend.items():
            if game_spend < MIN_GAME_SPEND_FOR_ALERT:
                continue
            game_revenue = per_game_revenue.get(game_id, 0.0)
            game_roas = game_revenue / game_spend
            if game_roas < 1.0:
                self._alert(
                    ctx, "warn",
                    f"{game_names.get(game_id, game_id)}: ROAS {game_roas:.2f} "
                    f"(${game_revenue:.2f} rev / ${game_spend:.2f} spend, last {WINDOW_DAYS}d)",
                    game_id=game_id,
                )


def get_module() -> Module:
    return RoasEngine()
