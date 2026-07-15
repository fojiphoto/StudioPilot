"""Core data model (SPEC section 6). History is always kept - never just a snapshot."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    store: Mapped[str] = mapped_column(String(20))  # ios | android | amazon
    package_name: Mapped[str | None] = mapped_column(String(200), index=True)  # bundle id, joins sources
    genre: Mapped[str | None] = mapped_column(String(100))
    launch_date: Mapped[date | None] = mapped_column(Date)
    dev_cost: Mapped[float] = mapped_column(Float, default=0.0)  # manual entry, feeds P&L

    __table_args__ = (UniqueConstraint("name", "store"),)


class AdRevenueRecord(Base):
    __tablename__ = "ad_revenue_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    date: Mapped[date] = mapped_column(Date, index=True)
    hour: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(50), index=True)  # which mediation reported it: applovin_max | admob
    network: Mapped[str] = mapped_column(String(50))   # serving ad network within the mediation
    country: Mapped[str | None] = mapped_column(String(2))
    platform: Mapped[str] = mapped_column(String(20))  # ios | android | amazon
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    ecpm: Mapped[float] = mapped_column(Float, default=0.0)
    revenue: Mapped[float] = mapped_column(Float, default=0.0)


class CampaignRecord(Base):
    __tablename__ = "campaign_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Nullable: campaigns arrive unmapped; a later mapping step ties them to games.
    game_id: Mapped[int | None] = mapped_column(ForeignKey("games.id"))
    date: Mapped[date] = mapped_column(Date, index=True)
    ua_platform: Mapped[str] = mapped_column(String(20))  # google | meta | mintegral
    campaign_id: Mapped[str] = mapped_column(String(100))
    campaign_name: Mapped[str | None] = mapped_column(String(200))
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    installs: Mapped[int] = mapped_column(Integer, default=0)
    cpi: Mapped[float] = mapped_column(Float, default=0.0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)


class GameMetricRecord(Base):
    __tablename__ = "game_metric_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    date: Mapped[date] = mapped_column(Date, index=True)
    country: Mapped[str | None] = mapped_column(String(2))
    dau: Mapped[int] = mapped_column(Integer, default=0)
    avg_playtime: Mapped[float] = mapped_column(Float, default=0.0)  # seconds
    retention_d1: Mapped[float | None] = mapped_column(Float)
    retention_d7: Mapped[float | None] = mapped_column(Float)
    retention_d30: Mapped[float | None] = mapped_column(Float)
    sessions: Mapped[int] = mapped_column(Integer, default=0)


class CohortLTV(Base):
    __tablename__ = "cohort_ltv"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    cohort_date: Mapped[date] = mapped_column(Date, index=True)
    day_n: Mapped[int] = mapped_column(Integer)
    cumulative_ltv: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (UniqueConstraint("game_id", "cohort_date", "day_n"),)


class AnalyticsGap(Base):
    __tablename__ = "analytics_gaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    event_name: Mapped[str] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(10))  # major | minor
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | fixed
    decision_blocked_note: Mapped[str | None] = mapped_column(Text)


class PnLSnapshot(Base):
    __tablename__ = "pnl_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    period: Mapped[str] = mapped_column(String(20))  # e.g. "2026-07" or "lifetime"
    ad_revenue: Mapped[float] = mapped_column(Float, default=0.0)
    iap_revenue: Mapped[float] = mapped_column(Float, default=0.0)
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    dev_cost: Mapped[float] = mapped_column(Float, default=0.0)
    net: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceSync(Base):
    """Per-source freshness - drives the 'last updated' label everywhere (SPEC section 4)."""

    __tablename__ = "source_syncs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50), unique=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    freshness_note: Mapped[str | None] = mapped_column(String(200))  # e.g. "D-1, lands ~1:30am"


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    severity: Mapped[str] = mapped_column(String(10))  # info | warn | critical
    module: Mapped[str] = mapped_column(String(50))
    game_id: Mapped[int | None] = mapped_column(ForeignKey("games.id"))
    message: Mapped[str] = mapped_column(Text)
    delivered_via: Mapped[str | None] = mapped_column(String(50))
    acknowledged: Mapped[int] = mapped_column(Integer, default=0)
