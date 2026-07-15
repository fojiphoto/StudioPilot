"""Module discovery. Walks gameos/modules/{connectors,analyzers,outputs}, imports every
package/file, and collects the object returned by its `get_module()` factory.
Dropping a new module file in is all that's needed - the kernel never changes."""
from __future__ import annotations

import importlib
import logging
import pkgutil

from gameos.kernel.config import Settings
from gameos.kernel.module import Module, ModuleType

log = logging.getLogger("gameos.registry")

_MODULE_PACKAGES = [
    "gameos.modules.connectors",
    "gameos.modules.analyzers",
    "gameos.modules.outputs",
]

# Execution order of a cycle: pull data, then analyze it, then deliver results.
CYCLE_ORDER = [ModuleType.CONNECTOR, ModuleType.ANALYZER, ModuleType.OUTPUT]


def discover(settings: Settings) -> list[Module]:
    modules: list[Module] = []
    for package_name in _MODULE_PACKAGES:
        package = importlib.import_module(package_name)
        for item in pkgutil.iter_modules(package.__path__):
            full_name = f"{package_name}.{item.name}"
            try:
                py_module = importlib.import_module(full_name)
            except Exception:
                log.exception("failed to import module %s - skipping", full_name)
                continue
            factory = getattr(py_module, "get_module", None)
            if factory is None:
                log.warning("%s has no get_module() factory - skipping", full_name)
                continue
            module = factory()
            if not isinstance(module, Module):
                log.warning("%s get_module() did not return a Module - skipping", full_name)
                continue
            if not settings.module_enabled(module.info.name):
                log.info("module %s disabled by config", module.info.name)
                continue
            modules.append(module)

    names = [m.info.name for m in modules]
    if len(names) != len(set(names)):
        raise RuntimeError(f"duplicate module names discovered: {names}")

    modules.sort(key=lambda m: CYCLE_ORDER.index(m.info.type))
    log.info("discovered %d modules: %s", len(modules), ", ".join(names) or "-")
    return modules
