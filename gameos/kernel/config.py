"""GameOS configuration - loaded from .env / environment. Owner-tunable at any time."""
from enum import Enum

from pydantic_settings import BaseSettings, SettingsConfigDict


class RunMode(str, Enum):
    CONTINUOUS = "continuous"  # 24/7, each module on its own cadence
    INTERVAL = "interval"      # wake every N minutes, full cycle, standby
    ONESHOT = "oneshot"        # one full cycle, then exit


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GAMEOS_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    db_url: str = "sqlite:///data/gameos.db"
    mode: RunMode = RunMode.INTERVAL
    interval_minutes: int = 10
    # Comma-separated module names to enable; "*" = every discovered module.
    enabled_modules: str = "*"
    # Comma-separated module names to disable (takes precedence over enabled_modules).
    # e.g. "admob" when AdMob is mediated through AppLovin MAX (its revenue is already
    # in the MAX total, so a standalone AdMob pull would double-count).
    disabled_modules: str = ""
    log_level: str = "INFO"

    def module_enabled(self, name: str) -> bool:
        disabled = {m.strip() for m in self.disabled_modules.split(",") if m.strip()}
        if name in disabled:
            return False
        if self.enabled_modules.strip() == "*":
            return True
        return name in {m.strip() for m in self.enabled_modules.split(",")}


def load_settings() -> Settings:
    return Settings()
