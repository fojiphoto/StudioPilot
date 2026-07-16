"""GameOS CLI: run the engine, self-test modules, print reports, list modules."""
from __future__ import annotations

import logging

import typer

from gameos.kernel.config import RunMode, load_settings
from gameos.kernel.runtime import Engine

app = typer.Typer(help="StudioPilot GameOS - UA & monetization agent")


def _parse_every(every: str) -> int:
    """'10m' -> 10, '2h' -> 120, '15' -> 15."""
    every = every.strip().lower()
    if every.endswith("h"):
        return int(every[:-1]) * 60
    if every.endswith("m"):
        return int(every[:-1])
    return int(every)


@app.command()
def run(
    mode: str = typer.Option(None, help="continuous | interval | oneshot (default from .env)"),
    every: str = typer.Option(None, help="Interval for interval mode, e.g. 2m, 10m, 1h"),
) -> None:
    """Start the GameOS engine."""
    settings = load_settings()
    if mode:
        settings.mode = RunMode(mode)
    if every:
        settings.interval_minutes = _parse_every(every)
    Engine(settings).start()


@app.command()
def modules() -> None:
    """List discovered modules."""
    engine = Engine()
    for module in engine.modules:
        info = module.info
        typer.echo(f"{info.type.value:9}  {info.name:20}  every {info.default_interval_minutes}m  {info.description}")


@app.command()
def test(name: str = typer.Argument(None, help="Module to self-test (omit for all)")) -> None:
    """Run module self-tests (connectors prove they return real data)."""
    engine = Engine()
    targets = [m for m in engine.modules if name is None or m.info.name == name]
    if not targets:
        typer.echo(f"no module named '{name}'", err=True)
        raise typer.Exit(1)
    failed = False
    for module in targets:
        ok, message = module.self_test(engine.ctx)
        status = "OK  " if ok else "FAIL"
        typer.echo(f"[{status}] {module.info.name}: {message}")
        failed = failed or not ok
    raise typer.Exit(1 if failed else 0)


@app.command()
def report() -> None:
    """Print current status: source freshness, ROAS, P&L, recent alerts."""
    from datetime import date, timedelta

    from sqlalchemy import func

    from gameos.kernel.models import AdRevenueRecord, Alert, CampaignRecord, Game, PnLSnapshot, SourceSync

    engine = Engine()
    with engine.ctx.session() as session:
        typer.echo("== Source freshness ==")
        syncs = session.query(SourceSync).order_by(SourceSync.source).all()
        if not syncs:
            typer.echo("  (no sources have synced yet)")
        for sync in syncs:
            note = f"  [{sync.freshness_note}]" if sync.freshness_note else ""
            typer.echo(f"  {sync.source:20} last ok: {sync.last_success_at}{note}")

        since = date.today() - timedelta(days=6)
        revenue = (
            session.query(func.coalesce(func.sum(AdRevenueRecord.revenue), 0.0))
            .filter(AdRevenueRecord.date >= since).scalar()
        )
        spend = (
            session.query(func.coalesce(func.sum(CampaignRecord.spend), 0.0))
            .filter(CampaignRecord.date >= since).scalar()
        )
        roas = f"{revenue / spend:.2f}" if spend else "n/a (no spend)"
        typer.echo("== Last 7 days (portfolio) ==")
        typer.echo(f"  revenue: ${revenue:,.2f}   spend: ${spend:,.2f}   ROAS: {roas}")

        typer.echo("== P&L (lifetime*, top games by net) ==")
        rows = (
            session.query(PnLSnapshot, Game.name, Game.store)
            .join(Game, Game.id == PnLSnapshot.game_id)
            .filter(PnLSnapshot.period == "lifetime")
            .order_by(PnLSnapshot.net.desc())
            .limit(15)
            .all()
        )
        if not rows:
            typer.echo("  (no P&L snapshots yet - run a cycle first)")
        for snap, name, store in rows:
            typer.echo(
                f"  {name[:34]:34} [{store:7}] rev ${snap.ad_revenue:9,.2f}  "
                f"spend ${snap.spend:8,.2f}  dev ${snap.dev_cost:8,.2f}  net ${snap.net:9,.2f}"
            )
        totals = (
            session.query(
                func.coalesce(func.sum(PnLSnapshot.ad_revenue), 0.0),
                func.coalesce(func.sum(PnLSnapshot.spend), 0.0),
                func.coalesce(func.sum(PnLSnapshot.dev_cost), 0.0),
                func.coalesce(func.sum(PnLSnapshot.net), 0.0),
            )
            .filter(PnLSnapshot.period == "lifetime")
            .one()
        )
        typer.echo(
            f"  {'PORTFOLIO':34} {'':9} rev ${totals[0]:9,.2f}  "
            f"spend ${totals[1]:8,.2f}  dev ${totals[2]:8,.2f}  net ${totals[3]:9,.2f}"
        )
        typer.echo("  (*lifetime = since GameOS started collecting; historical backfill pending)")

        typer.echo("== Recent alerts ==")
        alerts = session.query(Alert).order_by(Alert.created_at.desc()).limit(20).all()
        if not alerts:
            typer.echo("  (none)")
        for alert in alerts:
            typer.echo(f"  {alert.created_at:%Y-%m-%d %H:%M} [{alert.severity}] {alert.module}: {alert.message}")


if __name__ == "__main__":
    app()
