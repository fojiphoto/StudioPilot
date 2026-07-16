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
def dashboard(
    host: str = typer.Option("127.0.0.1", help="Bind address (0.0.0.0 to expose on LAN)"),
    port: int = typer.Option(8080),
) -> None:
    """Serve the optional read-only web dashboard (the engine never depends on it)."""
    import uvicorn

    from gameos.dashboard import app as dashboard_app

    uvicorn.run(dashboard_app, host=host, port=port, log_level="warning")


@app.command()
def collect(
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(8090),
) -> None:
    """Run the GameOS SDK event collector (always-on ingest endpoint)."""
    import uvicorn

    from gameos.collector import app as collector_app

    uvicorn.run(collector_app, host=host, port=port, log_level="warning")


@app.command(name="ingest-key")
def ingest_key(
    game_id: int = typer.Argument(None, help="Game id (omit with --all)"),
    all_games: bool = typer.Option(False, "--all", help="Mint keys for every game lacking one"),
    show: bool = typer.Option(False, "--show", help="List games that already have keys"),
) -> None:
    """Mint / list the per-game ingest key used by the GameOS SDK."""
    import secrets

    from gameos.kernel.models import Game

    engine = Engine()
    with engine.ctx.session() as session:
        if show:
            for game in session.query(Game).filter(Game.ingest_key.is_not(None)).all():
                typer.echo(f"  #{game.id:<5} {game.ingest_key}  {game.name}")
            return
        if all_games:
            games = session.query(Game).filter(Game.ingest_key.is_(None)).all()
        elif game_id is not None:
            game = session.get(Game, game_id)
            if game is None:
                typer.echo(f"no game with id {game_id}", err=True)
                raise typer.Exit(1)
            games = [game]
        else:
            typer.echo("give a game_id or --all", err=True)
            raise typer.Exit(1)
        for game in games:
            if not game.ingest_key:
                game.ingest_key = secrets.token_urlsafe(24)
            typer.echo(f"  #{game.id:<5} {game.ingest_key}  {game.name}")
        session.commit()


@app.command()
def backfill(
    name: str = typer.Argument(..., help="Connector to backfill (e.g. applovin_max)"),
    days: int = typer.Option(45, help="How many days back"),
) -> None:
    """Pull historical data as far back as the platform allows."""
    engine = Engine()
    module = next((m for m in engine.modules if m.info.name == name), None)
    if module is None:
        typer.echo(f"no module named '{name}'", err=True)
        raise typer.Exit(1)
    try:
        summary = module.backfill(engine.ctx, days)
    except NotImplementedError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"[OK] {name}: {summary}")


@app.command()
def games(search: str = typer.Argument(None, help="Filter by name substring")) -> None:
    """List games with their ids (for campaign mapping and dev-cost entry)."""
    from gameos.kernel.models import Game

    engine = Engine()
    with engine.ctx.session() as session:
        query = session.query(Game).order_by(Game.name)
        if search:
            query = query.filter(Game.name.ilike(f"%{search}%"))
        for game in query.all():
            pkg = f"  ({game.package_name})" if game.package_name else ""
            typer.echo(f"  #{game.id:<5} [{game.store:7}] {game.name}{pkg}")


@app.command()
def campaigns() -> None:
    """List known campaigns and their game mapping status."""
    from sqlalchemy import func

    from gameos.kernel.models import CampaignRecord, Game

    engine = Engine()
    with engine.ctx.session() as session:
        rows = (
            session.query(
                CampaignRecord.ua_platform,
                CampaignRecord.campaign_id,
                func.max(CampaignRecord.campaign_name),
                CampaignRecord.game_id,
                func.sum(CampaignRecord.spend),
            )
            .group_by(CampaignRecord.ua_platform, CampaignRecord.campaign_id, CampaignRecord.game_id)
            .all()
        )
        if not rows:
            typer.echo("  (no campaigns pulled yet)")
        game_names = dict(session.query(Game.id, Game.name).all())
        for platform, campaign_id, campaign_name, game_id, spend in rows:
            mapped = game_names.get(game_id, "!! UNMAPPED - use: gameos map ...")
            typer.echo(
                f"  [{platform:9}] {campaign_id:20} {(campaign_name or '-')[:36]:36} "
                f"spend ${spend or 0:8,.2f} -> {mapped}"
            )


@app.command(name="map")
def map_campaign(
    ua_platform: str = typer.Argument(..., help="google | meta | mintegral"),
    campaign_id: str = typer.Argument(...),
    game_id: int = typer.Argument(..., help="Game id from `gameos games`"),
) -> None:
    """Map a UA campaign to a game (applies to stored rows and all future pulls)."""
    from gameos.kernel.models import CampaignMap, CampaignRecord, Game

    engine = Engine()
    with engine.ctx.session() as session:
        game = session.get(Game, game_id)
        if game is None:
            typer.echo(f"no game with id {game_id} - run `gameos games`", err=True)
            raise typer.Exit(1)
        existing = (
            session.query(CampaignMap)
            .filter_by(ua_platform=ua_platform, campaign_id=campaign_id)
            .one_or_none()
        )
        if existing:
            existing.game_id = game_id
        else:
            session.add(CampaignMap(ua_platform=ua_platform, campaign_id=campaign_id, game_id=game_id))
        updated = (
            session.query(CampaignRecord)
            .filter_by(ua_platform=ua_platform, campaign_id=campaign_id)
            .update({CampaignRecord.game_id: game_id})
        )
        session.commit()
    typer.echo(f"mapped {ua_platform}/{campaign_id} -> {game.name} ({updated} existing rows updated)")


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
