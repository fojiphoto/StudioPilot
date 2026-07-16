"""P&L engine - true profit picture per game, including dev cost (SPEC 5.5).

Each cycle recomputes a "lifetime" PnLSnapshot per game that has any activity:
net = ad_revenue + iap_revenue - ua_spend - dev_cost.
dev_cost comes from the Game row (manual entry by the owner).
IAP revenue is 0 until an IAP source lands (Firebase, Phase 3).
"""
from __future__ import annotations

from sqlalchemy import func

from gameos.kernel.models import AdRevenueRecord, CampaignRecord, Game, PnLSnapshot
from gameos.kernel.module import Module, ModuleInfo, ModuleType
from gameos.kernel.runtime import Context


class PnLEngine(Module):
    info = ModuleInfo(
        name="pnl_engine",
        type=ModuleType.ANALYZER,
        description="Lifetime net P&L per game (ad revenue + IAP - UA spend - dev cost).",
        default_interval_minutes=180,
    )

    def run(self, ctx: Context) -> None:
        with ctx.session() as session:
            revenue_by_game = dict(
                session.query(AdRevenueRecord.game_id, func.sum(AdRevenueRecord.revenue))
                .group_by(AdRevenueRecord.game_id)
                .all()
            )
            spend_by_game = dict(
                session.query(CampaignRecord.game_id, func.sum(CampaignRecord.spend))
                .filter(CampaignRecord.game_id.is_not(None))
                .group_by(CampaignRecord.game_id)
                .all()
            )
            games = {g.id: g for g in session.query(Game).all()}

            snapshots = []
            for game_id, game in games.items():
                ad_revenue = float(revenue_by_game.get(game_id, 0.0) or 0.0)
                spend = float(spend_by_game.get(game_id, 0.0) or 0.0)
                dev_cost = float(game.dev_cost or 0.0)
                if ad_revenue == 0 and spend == 0 and dev_cost == 0:
                    continue  # nothing to report for this game yet
                snapshots.append(
                    PnLSnapshot(
                        game_id=game_id,
                        period="lifetime",
                        ad_revenue=ad_revenue,
                        iap_revenue=0.0,
                        spend=spend,
                        dev_cost=dev_cost,
                        net=ad_revenue + 0.0 - spend - dev_cost,
                    )
                )

            # Lifetime snapshots are a rolling recomputation - replace, don't append.
            session.query(PnLSnapshot).filter(PnLSnapshot.period == "lifetime").delete()
            session.add_all(snapshots)
            session.commit()

        total_net = sum(s.net for s in snapshots)
        ctx.cycle_state["pnl"] = {"games": len(snapshots), "portfolio_net": round(total_net, 2)}
        self.log.info("recomputed lifetime P&L for %d games, portfolio net $%.2f", len(snapshots), total_net)

        # NOTE: lifetime revenue/spend currently only cover what connectors have pulled
        # (MAX keeps 45 days of history; backfill job is a future item). Treat "lifetime"
        # as "since GameOS started collecting" until backfill exists.


def get_module() -> Module:
    return PnLEngine()
