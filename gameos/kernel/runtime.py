"""GameOS engine: builds the runtime Context and drives the three run modes.

continuous - runs 24/7; each module fires on its own cadence.
interval   - wakes every N minutes, runs one full cycle, goes back to standby.
oneshot    - runs one full cycle and exits.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv
from sqlalchemy.orm import Session, sessionmaker

from gameos.kernel import db, registry
from gameos.kernel.config import RunMode, Settings, load_settings
from gameos.kernel.models import SourceSync
from gameos.kernel.module import Module

log = logging.getLogger("gameos.runtime")


@dataclass
class Context:
    """Everything a module gets access to. Modules never import the kernel internals."""

    settings: Settings
    session_factory: sessionmaker[Session]
    # Scratch space for modules to hand results to later modules within one cycle
    # (e.g. analyzer leaves alerts, output module delivers them).
    cycle_state: dict = field(default_factory=dict)

    def session(self) -> Session:
        return self.session_factory()

    def mark_synced(self, source: str, freshness_note: str | None = None) -> None:
        """Connectors call this after a successful pull - drives 'last updated' labels."""
        with self.session() as session:
            row = session.query(SourceSync).filter_by(source=source).one_or_none()
            if row is None:
                row = SourceSync(source=source)
                session.add(row)
            row.last_success_at = datetime.now(timezone.utc)
            if freshness_note:
                row.freshness_note = freshness_note
            session.commit()


class Engine:
    def __init__(self, settings: Settings | None = None) -> None:
        load_dotenv()  # module credentials (APPLOVIN_*, ADMOB_*, ...) come from .env
        self.settings = settings or load_settings()
        logging.basicConfig(
            level=self.settings.log_level,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        )
        # httpx logs full request URLs at INFO - those can contain API keys. Keep it quiet.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        engine = db.make_engine(self.settings.db_url)
        self.ctx = Context(
            settings=self.settings,
            session_factory=db.make_session_factory(engine),
        )
        self.modules: list[Module] = registry.discover(self.settings)
        for module in self.modules:
            module.setup(self.ctx)

    def run_module(self, module: Module) -> None:
        try:
            module.run(self.ctx)
        except Exception:
            # One broken module must never take the whole agent down.
            log.exception("module %s failed", module.info.name)

    def run_cycle(self) -> None:
        """One full pass: connectors -> analyzers -> outputs (order from registry)."""
        self.ctx.cycle_state = {}
        log.info("cycle start (%d modules)", len(self.modules))
        for module in self.modules:
            self.run_module(module)
        log.info("cycle done")

    def start(self) -> None:
        mode = self.settings.mode
        log.info("GameOS starting in %s mode", mode.value)
        try:
            if mode is RunMode.ONESHOT:
                self.run_cycle()
            elif mode is RunMode.INTERVAL:
                scheduler = BlockingScheduler()
                scheduler.add_job(
                    self.run_cycle,
                    "interval",
                    minutes=self.settings.interval_minutes,
                    next_run_time=datetime.now(timezone.utc),  # first cycle immediately
                )
                scheduler.start()
            else:  # CONTINUOUS - each module on its own cadence
                scheduler = BlockingScheduler()
                for module in self.modules:
                    scheduler.add_job(
                        self.run_module,
                        "interval",
                        args=[module],
                        minutes=module.info.default_interval_minutes,
                        next_run_time=datetime.now(timezone.utc),
                    )
                scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            log.info("GameOS stopping")
        finally:
            for module in self.modules:
                module.teardown(self.ctx)
