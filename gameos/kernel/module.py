"""Module contract. Every connector/analyzer/output implements this - and nothing else
in the kernel needs to change when a new module is added (hard requirement, SPEC 5.10)."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gameos.kernel.runtime import Context


class ModuleType(str, Enum):
    CONNECTOR = "connector"  # pulls platform data into the DB
    ANALYZER = "analyzer"    # reads DB, computes metrics/suggestions/alerts
    OUTPUT = "output"        # delivers results (telegram, cli report, dashboard)


@dataclass(frozen=True)
class ModuleInfo:
    name: str                       # unique, e.g. "applovin_max"
    type: ModuleType
    description: str
    # Cadence hint used in `continuous` mode; interval/oneshot cycles run all modules.
    default_interval_minutes: int = 60


class Module(ABC):
    info: ModuleInfo

    def __init__(self) -> None:
        self.log = logging.getLogger(f"gameos.{self.info.name}")

    def setup(self, ctx: Context) -> None:
        """One-time init (auth clients, checks). Called once before first run."""

    @abstractmethod
    def run(self, ctx: Context) -> None:
        """One unit of work: pull, analyze, or deliver."""

    def teardown(self, ctx: Context) -> None:
        """Cleanup on shutdown."""

    def self_test(self, ctx: Context) -> tuple[bool, str]:
        """Prove the module actually works (e.g. connector returns real data).
        Returns (ok, message). Override in every connector (SPEC 5.1)."""
        return True, "no self-test implemented"
