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
    """Print current status: source freshness, recent alerts. (Grows with analyzers.)"""
    from gameos.kernel.models import Alert, SourceSync

    engine = Engine()
    with engine.ctx.session() as session:
        typer.echo("== Source freshness ==")
        syncs = session.query(SourceSync).order_by(SourceSync.source).all()
        if not syncs:
            typer.echo("  (no sources have synced yet)")
        for sync in syncs:
            note = f"  [{sync.freshness_note}]" if sync.freshness_note else ""
            typer.echo(f"  {sync.source:20} last ok: {sync.last_success_at}{note}")

        typer.echo("== Recent alerts ==")
        alerts = session.query(Alert).order_by(Alert.created_at.desc()).limit(20).all()
        if not alerts:
            typer.echo("  (none)")
        for alert in alerts:
            typer.echo(f"  {alert.created_at:%Y-%m-%d %H:%M} [{alert.severity}] {alert.module}: {alert.message}")


if __name__ == "__main__":
    app()
