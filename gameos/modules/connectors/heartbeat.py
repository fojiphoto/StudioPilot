"""Heartbeat - dummy connector proving the plugin system end-to-end.
It 'pulls' nothing but records a SourceSync row each run, so `gameos report`
shows the engine is alive. Safe to disable once real connectors exist."""
from __future__ import annotations

from gameos.kernel.module import Module, ModuleInfo, ModuleType
from gameos.kernel.runtime import Context


class Heartbeat(Module):
    info = ModuleInfo(
        name="heartbeat",
        type=ModuleType.CONNECTOR,
        description="Proves the engine is alive; records a sync timestamp each run.",
        default_interval_minutes=5,
    )

    def run(self, ctx: Context) -> None:
        ctx.mark_synced("heartbeat", freshness_note="engine liveness marker")
        self.log.info("alive")

    def self_test(self, ctx: Context) -> tuple[bool, str]:
        self.run(ctx)
        return True, "wrote SourceSync row"


def get_module() -> Module:
    return Heartbeat()
