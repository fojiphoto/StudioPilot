"""GameOS event collector - the always-on ingest endpoint for our own analytics SDK.

This is the "own MMP-lite" piece: games POST session/engagement events here, we store
them, and the engagement_metrics analyzer rolls them into DAU/MAU/retention/playtime.

Run standalone:  gameos collect --host 0.0.0.0 --port 8090
Auth: each game has an ingest_key (see `gameos ingest-key`). The SDK sends it as game_key.

Deliberately tiny and dependency-light so it can run 24/7 next to the engine or in Docker.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from gameos.kernel import db
from gameos.kernel.config import load_settings
from gameos.kernel.models import Device, Game, SessionEvent

app = FastAPI(title="GameOS Collector", docs_url=None, redoc_url=None)
_session_factory = None
_key_cache: dict[str, int] = {}  # ingest_key -> game_id

VALID_EVENTS = {"install", "session_start", "session_end"}


def session_factory():
    global _session_factory
    if _session_factory is None:
        settings = load_settings()
        _session_factory = db.make_session_factory(db.make_engine(settings.db_url))
    return _session_factory


class Event(BaseModel):
    game_key: str
    device_id: str = Field(min_length=1, max_length=128)
    event_type: str
    ts: datetime | None = None          # client event time; server time if omitted
    duration_sec: float = 0.0           # for session_end
    platform: str | None = None
    country: str | None = None


def _resolve_game(session, game_key: str) -> int:
    if game_key in _key_cache:
        return _key_cache[game_key]
    game = session.query(Game).filter_by(ingest_key=game_key).one_or_none()
    if game is None:
        raise HTTPException(status_code=401, detail="invalid game_key")
    _key_cache[game_key] = game.id
    return game.id


@app.post("/collect")
def collect(event: Event):
    if event.event_type not in VALID_EVENTS:
        raise HTTPException(status_code=400, detail=f"unknown event_type '{event.event_type}'")
    when = (event.ts or datetime.now(timezone.utc)).astimezone(timezone.utc)
    day = when.date()

    with session_factory()() as s:
        game_id = _resolve_game(s, event.game_key)
        s.add(SessionEvent(
            game_id=game_id, device_id=event.device_id, event_type=event.event_type,
            date=day, duration_sec=max(0.0, event.duration_sec),
        ))
        device = (
            s.query(Device).filter_by(game_id=game_id, device_id=event.device_id).one_or_none()
        )
        if device is None:
            s.add(Device(
                game_id=game_id, device_id=event.device_id, platform=event.platform,
                country=event.country, first_seen=day, last_seen=day,
            ))
        else:
            if day > device.last_seen:
                device.last_seen = day
            if day < device.first_seen:  # late-arriving install event
                device.first_seen = day
            if event.country and not device.country:
                device.country = event.country
        s.commit()
    return {"ok": True}


@app.get("/health")
def health():
    return {"ok": True, "service": "gameos-collector"}
