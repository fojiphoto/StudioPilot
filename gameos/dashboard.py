"""Read-only dashboard - OPTIONAL view over the GameOS DB (SPEC 5.9).

Run with: gameos dashboard [--port 8080]
The engine never depends on this; it only reads what the daemon has stored.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import func

from gameos.kernel import db
from gameos.kernel.config import load_settings
from gameos.kernel.models import (
    AdRevenueRecord, Alert, CampaignRecord, Game, GameMetricRecord, PnLSnapshot, SourceSync,
)

app = FastAPI(title="GameOS Dashboard", docs_url=None, redoc_url=None)
_session_factory = None


def session_factory():
    global _session_factory
    if _session_factory is None:
        settings = load_settings()
        _session_factory = db.make_session_factory(db.make_engine(settings.db_url))
    return _session_factory


def _range(start: str | None, end: str | None, default_days: int = 30) -> tuple[date, date]:
    end_date = date.fromisoformat(end) if end else date.today()
    start_date = date.fromisoformat(start) if start else end_date - timedelta(days=default_days - 1)
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    return start_date, end_date


@app.get("/api/range")
def data_range():
    """Earliest/latest dates for which any data exists - bounds the calendar."""
    with session_factory()() as s:
        rev_min, rev_max = s.query(func.min(AdRevenueRecord.date), func.max(AdRevenueRecord.date)).one()
        sp_min, sp_max = s.query(func.min(CampaignRecord.date), func.max(CampaignRecord.date)).one()
    candidates_min = [d for d in (rev_min, sp_min) if d]
    candidates_max = [d for d in (rev_max, sp_max) if d]
    return {
        "min": min(candidates_min).isoformat() if candidates_min else date.today().isoformat(),
        "max": max(candidates_max).isoformat() if candidates_max else date.today().isoformat(),
    }


def _store_filter(query, model, store):
    """Restrict a revenue/spend query to one store by joining Game. store None/'all' = no filter."""
    if store and store != "all":
        query = query.join(Game, Game.id == model.game_id).filter(Game.store == store)
    return query


@app.get("/api/daily")
def daily(start: str | None = None, end: str | None = None, store: str | None = None):
    start_date, end_date = _range(start, end)
    with session_factory()() as s:
        rq = _store_filter(
            s.query(AdRevenueRecord.date, func.sum(AdRevenueRecord.revenue))
            .filter(AdRevenueRecord.date >= start_date, AdRevenueRecord.date <= end_date),
            AdRevenueRecord, store)
        sq = _store_filter(
            s.query(CampaignRecord.date, func.sum(CampaignRecord.spend))
            .filter(CampaignRecord.date >= start_date, CampaignRecord.date <= end_date),
            CampaignRecord, store)
        revenue = dict(rq.group_by(AdRevenueRecord.date).all())
        spend = dict(sq.group_by(CampaignRecord.date).all())
    days_list = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
    return {
        "labels": [d.isoformat() for d in days_list],
        "revenue": [round(float(revenue.get(d, 0) or 0), 2) for d in days_list],
        "spend": [round(float(spend.get(d, 0) or 0), 2) for d in days_list],
    }


@app.get("/api/top-games")
def top_games(start: str | None = None, end: str | None = None, limit: int = 12, store: str | None = None):
    start_date, end_date = _range(start, end)
    with session_factory()() as s:
        q = (
            s.query(Game.id, Game.name, Game.display_name, Game.store, Game.icon_url,
                    func.sum(AdRevenueRecord.revenue).label("rev"))
            .join(Game, Game.id == AdRevenueRecord.game_id)
            .filter(AdRevenueRecord.date >= start_date, AdRevenueRecord.date <= end_date)
        )
        if store and store != "all":
            q = q.filter(Game.store == store)
        rows = q.group_by(Game.id).order_by(func.sum(AdRevenueRecord.revenue).desc()).limit(limit).all()
    return [
        {"id": gid, "name": disp or name, "store": st, "icon": icon, "revenue": round(float(rev or 0), 2)}
        for gid, name, disp, st, icon, rev in rows
    ]


@app.get("/api/game/{game_id}")
def game_detail(game_id: int, start: str | None = None, end: str | None = None):
    """Everything the per-game page needs: daily revenue/spend/DAU/MAU/ARPDAU/ARPMAU,
    playtime and retention. Metric fields are null until an analytics source
    (GameAnalytics / Firebase) is connected - the charts show what exists."""
    start_date, end_date = _range(start, end)
    with session_factory()() as s:
        game = s.get(Game, game_id)
        if game is None:
            return {"error": "no such game"}
        revenue = dict(
            s.query(AdRevenueRecord.date, func.sum(AdRevenueRecord.revenue))
            .filter(AdRevenueRecord.game_id == game_id,
                    AdRevenueRecord.date >= start_date, AdRevenueRecord.date <= end_date)
            .group_by(AdRevenueRecord.date).all()
        )
        spend = dict(
            s.query(CampaignRecord.date, func.sum(CampaignRecord.spend))
            .filter(CampaignRecord.game_id == game_id,
                    CampaignRecord.date >= start_date, CampaignRecord.date <= end_date)
            .group_by(CampaignRecord.date).all()
        )
        # country=NULL rows are the per-game daily rollup; country rows come later
        metrics = {
            m.date: m
            for m in s.query(GameMetricRecord)
            .filter(GameMetricRecord.game_id == game_id, GameMetricRecord.country.is_(None),
                    GameMetricRecord.date >= start_date, GameMetricRecord.date <= end_date)
            .all()
        }
        networks = (
            s.query(AdRevenueRecord.network, func.sum(AdRevenueRecord.revenue))
            .filter(AdRevenueRecord.game_id == game_id,
                    AdRevenueRecord.date >= start_date, AdRevenueRecord.date <= end_date)
            .group_by(AdRevenueRecord.network)
            .order_by(func.sum(AdRevenueRecord.revenue).desc()).limit(10).all()
        )

    days_list = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]

    def metric(day, attr):
        m = metrics.get(day)
        value = getattr(m, attr) if m else None
        return value if value not in (None, 0) else None

    daily_revenue = [round(float(revenue.get(d, 0) or 0), 2) for d in days_list]
    daily_dau = [metric(d, "dau") for d in days_list]
    daily_mau = [metric(d, "mau") for d in days_list]
    return {
        "game": {"id": game.id, "name": game.label, "store": game.store, "icon": game.icon_url,
                 "package": game.package_name or game.name, "dev_cost": game.dev_cost},
        "labels": [d.isoformat() for d in days_list],
        "revenue": daily_revenue,
        "spend": [round(float(spend.get(d, 0) or 0), 2) for d in days_list],
        "dau": daily_dau,
        "mau": daily_mau,
        "arpdau": [
            round(r / u, 4) if (u and u > 0) else None
            for r, u in zip(daily_revenue, daily_dau)
        ],
        "arpmau": [
            round(r / u, 4) if (u and u > 0) else None
            for r, u in zip(daily_revenue, daily_mau)
        ],
        "avg_playtime_min": [
            round(metric(d, "avg_playtime") / 60, 1) if metric(d, "avg_playtime") else None
            for d in days_list
        ],
        "retention": {
            "d1": [metric(d, "retention_d1") for d in days_list],
            "d7": [metric(d, "retention_d7") for d in days_list],
            "d30": [metric(d, "retention_d30") for d in days_list],
        },
        "networks": {
            "labels": [n or "unknown" for n, _ in networks],
            "revenue": [round(float(r or 0), 2) for _, r in networks],
        },
        "has_metrics": any(v is not None for v in daily_dau),
    }


@app.get("/api/stats")
def stats(start: str | None = None, end: str | None = None, store: str | None = None):
    """Revenue/spend/ROAS for the selected range (drives the top cards)."""
    start_date, end_date = _range(start, end)
    with session_factory()() as s:
        revenue = _store_filter(
            s.query(func.coalesce(func.sum(AdRevenueRecord.revenue), 0.0)).filter(
                AdRevenueRecord.date >= start_date, AdRevenueRecord.date <= end_date),
            AdRevenueRecord, store).scalar()
        spend = _store_filter(
            s.query(func.coalesce(func.sum(CampaignRecord.spend), 0.0)).filter(
                CampaignRecord.date >= start_date, CampaignRecord.date <= end_date),
            CampaignRecord, store).scalar()
    roas = (revenue / spend) if spend else None
    return {
        "days": (end_date - start_date).days + 1,
        "revenue": round(revenue, 2),
        "spend": round(spend, 2),
        "roas": round(roas, 2) if roas is not None else None,
    }


@app.get("/api/summary")
def summary(store: str | None = None):
    """Lifetime P&L totals + game count (store-filterable), plus global sources/alerts."""
    with session_factory()() as s:
        totals_q = s.query(
            func.coalesce(func.sum(PnLSnapshot.ad_revenue), 0.0),
            func.coalesce(func.sum(PnLSnapshot.spend), 0.0),
            func.coalesce(func.sum(PnLSnapshot.net), 0.0),
        ).filter(PnLSnapshot.period == "lifetime")
        games_q = s.query(func.count(Game.id))
        if store and store != "all":
            totals_q = totals_q.join(Game, Game.id == PnLSnapshot.game_id).filter(Game.store == store)
            games_q = games_q.filter(Game.store == store)
        totals = totals_q.one()
        games_count = games_q.scalar()
        syncs = [
            {"source": x.source, "last": x.last_success_at.isoformat() if x.last_success_at else None,
             "note": x.freshness_note}
            for x in s.query(SourceSync).order_by(SourceSync.source).all()
        ]
        alerts = [
            {"at": a.created_at.strftime("%Y-%m-%d %H:%M"), "severity": a.severity,
             "module": a.module, "message": a.message}
            for a in s.query(Alert).order_by(Alert.created_at.desc()).limit(15).all()
        ]
    return {
        "lifetime": {"revenue": round(totals[0], 2), "spend": round(totals[1], 2), "net": round(totals[2], 2)},
        "games": games_count, "sources": syncs, "alerts": alerts,
    }


@app.get("/api/pnl")
def pnl(limit: int = 15, store: str | None = None):
    with session_factory()() as s:
        q = (
            s.query(PnLSnapshot, Game.id, Game.name, Game.display_name, Game.store, Game.icon_url)
            .join(Game, Game.id == PnLSnapshot.game_id)
            .filter(PnLSnapshot.period == "lifetime")
        )
        if store and store != "all":
            q = q.filter(Game.store == store)
        rows = q.order_by(PnLSnapshot.net.desc()).limit(limit).all()
    return [
        {"id": gid, "game": disp or name, "store": st, "icon": icon, "revenue": round(p.ad_revenue, 2),
         "spend": round(p.spend, 2), "dev_cost": round(p.dev_cost, 2), "net": round(p.net, 2)}
        for p, gid, name, disp, st, icon in rows
    ]


@app.get("/api/games")
def games_list(q: str | None = None, store: str | None = None, limit: int = 40):
    """Search games by name/package (for the per-game selector). Respects store filter."""
    with session_factory()() as s:
        query = s.query(Game.id, Game.name, Game.display_name, Game.store, Game.icon_url)
        if q:
            like = f"%{q}%"
            query = query.filter(
                (Game.name.ilike(like)) | (Game.package_name.ilike(like)) | (Game.display_name.ilike(like))
            )
        if store and store != "all":
            query = query.filter(Game.store == store)
        rows = query.order_by(Game.display_name.is_(None), Game.display_name, Game.name).limit(limit).all()
    return [{"id": i, "name": disp or n, "store": st, "icon": icon} for i, n, disp, st, icon in rows]


PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>GameOS</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, sans-serif; color:#e6e6e6;
    background: radial-gradient(1200px 500px at 15% -10%, #1a2740 0%, #0f1115 55%) no-repeat, #0f1115; }
  header { padding:16px 24px; border-bottom:1px solid #1e2530; display:flex; align-items:center; gap:14px; flex-wrap:wrap;
    background:rgba(13,17,23,.6); backdrop-filter:blur(6px); position:sticky; top:0; z-index:30; }
  .logo { width:34px; height:34px; border-radius:9px; display:grid; place-items:center; font-size:18px;
    background:linear-gradient(135deg,#3b82f6,#8b5cf6); box-shadow:0 4px 14px rgba(59,130,246,.35); }
  header h1 { margin:0; font-size:19px; font-weight:700; letter-spacing:.2px; }
  header span { color:#8a8f98; font-size:13px; }
  .wrap { padding:20px 24px; max-width:1140px; margin:0 auto; }
  .toolbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:14px; }
  .toolbar button { background:#161a22; border:1px solid #23262e; color:#cbd2dc; border-radius:8px;
    padding:6px 13px; cursor:pointer; font-size:13px; transition:all .12s; }
  .toolbar button:hover { border-color:#3b82f6; color:#fff; }
  .toolbar button.active { background:linear-gradient(135deg,#2563eb,#4f46e5); border-color:#4f46e5; color:#fff;
    box-shadow:0 2px 10px rgba(37,99,235,.4); }
  .toolbar input[type=date] { background:#161a22; border:1px solid #23262e; color:#e6e6e6;
    border-radius:8px; padding:5px 8px; font-size:13px; }
  .toolbar .sep { color:#3a4150; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(165px,1fr)); gap:12px; }
  .card { background:linear-gradient(180deg,#171c26,#12161e); border:1px solid #23262e; border-radius:12px;
    padding:15px 17px; position:relative; overflow:hidden; }
  .card::before { content:""; position:absolute; left:0; top:0; bottom:0; width:3px; background:#3b82f6; opacity:.8; }
  .card .k { color:#8a8f98; font-size:11px; text-transform:uppercase; letter-spacing:.06em; }
  .card .v { font-size:24px; font-weight:700; margin-top:5px; }
  .pos { color:#4ade80; } .neg { color:#f87171; }
  /* store badges */
  .badge { display:inline-flex; align-items:center; gap:4px; font-size:10px; font-weight:600; padding:2px 7px;
    border-radius:20px; text-transform:uppercase; letter-spacing:.03em; vertical-align:middle; }
  .badge svg { width:11px; height:11px; }
  .badge.amazon { background:rgba(255,153,0,.16); color:#ff9900; }
  .badge.android { background:rgba(61,220,132,.15); color:#3ddc84; }
  .badge.ios { background:rgba(200,209,220,.15); color:#c8d1dc; }
  /* game icon */
  .gicon { width:34px; height:34px; border-radius:9px; object-fit:cover; background:#23262e; flex:none; }
  .gicon.sm { width:24px; height:24px; border-radius:6px; }
  .gicon.ph { display:grid; place-items:center; font-size:13px; font-weight:700; color:#8a8f98; }
  /* leaderboard */
  .leaderboard { display:flex; flex-direction:column; gap:2px; }
  .lb-row { display:flex; align-items:center; gap:12px; padding:8px 10px; border-radius:9px; cursor:pointer;
    transition:background .12s; }
  .lb-row:hover { background:#1b212c; }
  .lb-rank { width:20px; text-align:right; color:#6b7280; font-size:12px; font-variant-numeric:tabular-nums; }
  .lb-main { flex:1; min-width:0; }
  .lb-name { font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .lb-bar { height:5px; border-radius:3px; margin-top:5px; background:linear-gradient(90deg,#3b82f6,#8b5cf6); }
  .lb-rev { font-size:13px; font-weight:600; font-variant-numeric:tabular-nums; white-space:nowrap; }
  .panel { background:#161a22; border:1px solid #23262e; border-radius:10px; padding:16px; margin-top:16px; }
  .panel h2 { margin:0 0 10px; font-size:14px; color:#8a8f98; font-weight:600; text-transform:uppercase; letter-spacing:.05em; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid #23262e; }
  th { color:#8a8f98; font-weight:500; } td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .muted { color:#8a8f98; font-size:12px; }
  canvas { max-height:300px; }
  .gamesearch-wrap { position:relative; }
  #gamesearch { background:#161a22; border:1px solid #23262e; color:#e6e6e6; border-radius:8px;
    padding:6px 10px; font-size:13px; min-width:240px; }
  #gameresults { position:absolute; top:110%; left:0; z-index:20; min-width:280px; max-height:320px;
    overflow-y:auto; background:#161a22; border:1px solid #2b3448; border-radius:8px; display:none; }
  #gameresults div { padding:7px 12px; cursor:pointer; font-size:13px; border-bottom:1px solid #23262e; }
  #gameresults div { display:flex; align-items:center; gap:9px; }
  #gameresults div:hover { background:#1d4ed8; }
  #gameresults .st { color:#8a8f98; font-size:11px; margin-left:auto; }
  .panel { background:linear-gradient(180deg,#161b24,#12161e); border:1px solid #23262e; border-radius:12px; padding:16px 18px; margin-top:16px; }
</style></head><body>
<header>
  <div class="logo">&#127918;</div>
  <div><h1>GameOS</h1></div>
  <span>UA &amp; monetization command center</span>
</header>
<div class="wrap">
  <div class="toolbar" id="toolbar">
    <button data-days="1">1D</button>
    <button data-days="3">3D</button>
    <button data-days="7" class="active">7D</button>
    <button data-days="15">15D</button>
    <button data-days="30">30D</button>
    <button data-days="all">All</button>
    <span class="sep">|</span>
    <input type="date" id="from"> <span class="sep">to</span> <input type="date" id="to">
    <span class="muted" id="rangeinfo"></span>
  </div>
  <div class="toolbar" id="storebar">
    <span class="muted" style="margin-right:4px">Store:</span>
    <button data-store="all" class="active">All</button>
    <button data-store="amazon">Amazon</button>
    <button data-store="android">Android</button>
    <button data-store="ios">iOS</button>
    <span class="sep">|</span>
    <span class="gamesearch-wrap">
      <input id="gamesearch" placeholder="&#128269; open any game by name..." autocomplete="off">
      <div id="gameresults"></div>
    </span>
  </div>
  <div class="cards" id="cards"></div>
  <div class="panel"><h2 id="dailyTitle">Revenue vs Spend</h2><canvas id="daily"></canvas></div>
  <div class="panel"><h2 id="topTitle">Top games by revenue</h2><div id="top" class="leaderboard"></div></div>
  <div class="panel"><h2>P&amp;L (lifetime, top by net)</h2><table id="pnl"></table>
    <div class="muted">lifetime = since GameOS started collecting (45-day backfill)</div></div>
  <div class="panel"><h2>Alerts</h2><table id="alerts"></table></div>
  <div class="panel"><h2>Source freshness</h2><table id="sources"></table></div>
</div>
<script>
const $ = (id) => document.getElementById(id);
const usd = (v) => '$' + Number(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
async function j(url) { return (await fetch(url)).json(); }
const iso = (d) => d.toISOString().slice(0,10);
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

const STORE_GLYPH = {
  amazon: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M18.3 16.4C16.6 17.7 14.1 18.4 12 18.4c-3 0-5.6-1.1-7.6-2.9-.2-.1 0-.3.1-.2 2.2 1.3 4.8 2 7.6 2 1.9 0 3.9-.4 5.8-1.2.3-.1.5.2.4.5zM19 15.3c-.2-.3-1.5-.1-2.1 0-.2 0-.2-.1 0-.3.9-.7 2.5-.5 2.7-.2.2.2 0 1.7-.9 2.4-.1.1-.3 0-.2-.1.2-.5.6-1.5.5-1.8z"/><path d="M12.3 12.2v-.9c0-.1.1-.2.2-.2h3.9c.1 0 .2.1.2.2v.8c0 .1-.1.3-.3.5l-2 2.9c.8 0 1.6.1 2.3.5.1.1.2.2.2.3v.9c0 .1-.2.3-.3.2-1.2-.6-2.8-.7-4.1 0-.1.1-.3-.1-.3-.2v-.9c0-.1 0-.3.2-.5l2.3-3.3h-2c-.2 0-.3-.1-.3-.2zM7 15.6H5.8c-.1 0-.2-.1-.2-.2V8.3c0-.1.1-.2.2-.2h1.1c.1 0 .2.1.2.2v.9c.3-.8 1-1.2 1.8-1.2.9 0 1.4.4 1.7 1.2.3-.8 1.1-1.2 1.9-1.2.6 0 1.2.2 1.6.8.4.6.3 1.5.3 2.2v3.4c0 .1-.1.2-.2.2h-1.2c-.1 0-.2-.1-.2-.2v-2.9c0-.3 0-1-.1-1.2-.1-.4-.4-.5-.7-.5-.3 0-.5.2-.7.5-.1.3-.1.9-.1 1.2v2.9c0 .1-.1.2-.2.2h-1.2c-.1 0-.2-.1-.2-.2v-2.9c0-.7.1-1.7-.8-1.7-.9 0-.8 1-.8 1.7v2.9c0 .1-.1.2-.2.2z"/></svg>',
  android: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 9v7a1 1 0 001 1h1v3a1 1 0 002 0v-3h2v3a1 1 0 002 0v-3h1a1 1 0 001-1V9H4zM2.5 9A1.5 1.5 0 001 10.5v4a1.5 1.5 0 003 0v-4A1.5 1.5 0 002.5 9zm17 0a1.5 1.5 0 00-1.5 1.5v4a1.5 1.5 0 003 0v-4A1.5 1.5 0 0019.5 9zM15 3.6l1-1.6a.3.3 0 00-.5-.3l-1.1 1.7A5.5 5.5 0 0012 3c-.9 0-1.7.2-2.4.5L8.5 1.7a.3.3 0 00-.5.3l1 1.6C7.4 4.5 6.3 6 6 8h12c-.3-2-1.4-3.5-3-4.4zM9.5 6a.6.6 0 110-1.2.6.6 0 010 1.2zm5 0a.6.6 0 110-1.2.6.6 0 010 1.2z"/></svg>',
  ios: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M16 12.5c0-2 1.6-3 1.7-3.1-1-1.4-2.4-1.6-2.9-1.6-1.2-.1-2.4.7-3 .7-.6 0-1.6-.7-2.6-.7-1.3 0-2.6.8-3.3 2-1.4 2.4-.4 6 1 8 .7 1 1.5 2.1 2.5 2 1-.1 1.4-.6 2.6-.6s1.5.6 2.6.6 1.7-1 2.4-2c.7-1.1 1-2.1 1-2.2-.1 0-2-.8-2-3.1zM14.2 6.3c.5-.7.9-1.6.8-2.5-.8 0-1.7.5-2.3 1.2-.5.6-.9 1.5-.8 2.4.9.1 1.7-.4 2.3-1.1z"/></svg>',
};
const badge = (store) => `<span class="badge ${store}">${STORE_GLYPH[store]||''}${store}</span>`;
const gicon = (url, name, cls='') => url
  ? `<img class="gicon ${cls}" src="${esc(url)}" referrerpolicy="no-referrer" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'gicon ph ${cls}',textContent:'${esc((name||'?')[0].toUpperCase())}'}))">`
  : `<div class="gicon ph ${cls}">${esc((name||'?')[0].toUpperCase())}</div>`;

let bounds = null, lifetime = null, dailyChart = null;
let store = 'all', curStart = null, curEnd = null;

function setActive(btn) {
  document.querySelectorAll('#toolbar button').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
}

async function loadSummary() {
  const sum = await j('/api/summary?store=' + store);
  lifetime = { net: sum.lifetime.net, games: sum.games };
  const p = await j('/api/pnl?store=' + store);
  $('pnl').innerHTML = '<tr><th></th><th>Game</th><th class="num">Revenue</th><th class="num">Spend</th><th class="num">Dev cost</th><th class="num">Net</th></tr>' +
    (p.length ? p.map(r => `<tr style="cursor:pointer" onclick="window.location='/game/${r.id}?start=${curStart}&end=${curEnd}'">
      <td style="width:32px">${gicon(r.icon, r.game, 'sm')}</td>
      <td>${esc(r.game)} ${badge(r.store)}</td>
      <td class="num">${usd(r.revenue)}</td><td class="num">${usd(r.spend)}</td>
      <td class="num">${usd(r.dev_cost)}</td><td class="num ${r.net>=0?'pos':'neg'}">${usd(r.net)}</td></tr>`).join('')
      : '<tr><td class="muted">no data for this store</td></tr>');
  return sum;
}

async function loadRange(start, end) {
  curStart = start; curEnd = end;
  const qs = `start=${start}&end=${end}&store=${store}`;
  const [st, d, t] = await Promise.all([j('/api/stats?'+qs), j('/api/daily?'+qs), j('/api/top-games?'+qs)]);
  const roas = st.roas === null ? 'n/a' : st.roas.toFixed(2);
  const roasCls = st.roas === null ? '' : (st.roas >= 1 ? 'pos' : 'neg');
  $('cards').innerHTML = `
    <div class="card"><div class="k">Revenue (${st.days}d)</div><div class="v">${usd(st.revenue)}</div></div>
    <div class="card"><div class="k">Spend (${st.days}d)</div><div class="v">${usd(st.spend)}</div></div>
    <div class="card"><div class="k">ROAS (${st.days}d)</div><div class="v ${roasCls}">${roas}</div></div>
    <div class="card"><div class="k">Net (lifetime)</div><div class="v ${lifetime.net>=0?'pos':'neg'}">${usd(lifetime.net)}</div></div>
    <div class="card"><div class="k">Games</div><div class="v">${lifetime.games}</div></div>`;
  $('dailyTitle').textContent = `Revenue vs Spend (${start} to ${end})`;
  $('topTitle').textContent = `Top games by revenue (${start} to ${end})`;
  $('rangeinfo').textContent = `data available: ${bounds.min} to ${bounds.max}`;

  if (dailyChart) { dailyChart.data.labels = d.labels;
    dailyChart.data.datasets[0].data = d.revenue; dailyChart.data.datasets[1].data = d.spend;
    dailyChart.update(); }
  else dailyChart = new Chart($('daily'), { type:'line', data:{ labels:d.labels, datasets:[
      {label:'Revenue', data:d.revenue, borderColor:'#4ade80', backgroundColor:'transparent', tension:.25},
      {label:'Spend', data:d.spend, borderColor:'#f87171', backgroundColor:'transparent', tension:.25}]},
    options:{ plugins:{legend:{labels:{color:'#e6e6e6'}}}, scales:{
      x:{ticks:{color:'#8a8f98', maxTicksLimit:10}, grid:{color:'#23262e'}},
      y:{ticks:{color:'#8a8f98'}, grid:{color:'#23262e'}}}}});

  const maxRev = Math.max(1, ...t.map(g => g.revenue));
  $('top').innerHTML = t.length ? t.map((g, i) => `
    <div class="lb-row" onclick="window.location='/game/${g.id}?start=${start}&end=${end}'">
      <div class="lb-rank">${i+1}</div>
      ${gicon(g.icon, g.name)}
      <div class="lb-main">
        <div class="lb-name">${esc(g.name)} ${badge(g.store)}</div>
        <div class="lb-bar" style="width:${Math.max(3, g.revenue/maxRev*100)}%"></div>
      </div>
      <div class="lb-rev">${usd(g.revenue)}</div>
    </div>`).join('') : '<div class="muted">no revenue in this range</div>';

  $('from').value = start; $('to').value = end;
}

function presetRange(days) {
  const end = bounds.max;
  if (days === 'all') return [bounds.min, end];
  const e = new Date(end + 'T00:00:00Z');
  const s = new Date(e); s.setUTCDate(e.getUTCDate() - (days - 1));
  const minD = new Date(bounds.min + 'T00:00:00Z');
  return [iso(s < minD ? minD : s), end];
}

(async () => {
  const [b, sum] = await Promise.all([j('/api/range'), loadSummary()]);
  bounds = b;
  $('from').min = b.min; $('from').max = b.max; $('to').min = b.min; $('to').max = b.max;

  // sources + alerts are global (not store-filtered)
  $('sources').innerHTML = '<tr><th>Source</th><th>Last OK</th><th>Freshness</th></tr>' +
    sum.sources.map(x => `<tr><td>${x.source}</td><td>${x.last ? x.last.replace('T',' ').slice(0,16) : '-'}</td><td class="muted">${x.note||''}</td></tr>`).join('');
  $('alerts').innerHTML = sum.alerts.length
    ? '<tr><th>When</th><th>Severity</th><th>Module</th><th>Message</th></tr>' +
      sum.alerts.map(a => `<tr><td>${a.at}</td><td class="${a.severity==='warn'||a.severity==='critical'?'neg':''}">${a.severity}</td><td>${a.module}</td><td>${a.message}</td></tr>`).join('')
    : '<tr><td class="muted">no alerts</td></tr>';

  document.querySelectorAll('#toolbar button').forEach(btn => btn.addEventListener('click', () => {
    setActive(btn);
    const v = btn.dataset.days;
    const [s, e] = presetRange(v === 'all' ? 'all' : parseInt(v));
    loadRange(s, e);
  }));
  const onDateChange = () => {
    if (!$('from').value || !$('to').value) return;
    setActive(null);
    loadRange($('from').value, $('to').value);
  };
  $('from').addEventListener('change', onDateChange);
  $('to').addEventListener('change', onDateChange);

  // store filter: refetch lifetime cards/pnl + current range
  document.querySelectorAll('#storebar button').forEach(btn => btn.addEventListener('click', async () => {
    document.querySelectorAll('#storebar button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    store = btn.dataset.store;
    await loadSummary();
    loadRange(curStart, curEnd);
  }));

  // per-game search: type -> matching games -> click opens that game's page
  const box = $('gamesearch'), results = $('gameresults');
  let searchTimer = null;
  box.addEventListener('input', () => {
    clearTimeout(searchTimer);
    const q = box.value.trim();
    if (!q) { results.style.display = 'none'; return; }
    searchTimer = setTimeout(async () => {
      const list = await j(`/api/games?q=${encodeURIComponent(q)}&store=${store}`);
      results.innerHTML = list.length
        ? list.map(g => `<div data-id="${g.id}">${gicon(g.icon, g.name, 'sm')}<span>${esc(g.name)}</span>${badge(g.store)}</div>`).join('')
        : '<div class="muted" style="cursor:default">no match</div>';
      results.style.display = 'block';
      results.querySelectorAll('div[data-id]').forEach(el => el.addEventListener('click', () => {
        window.location = `/game/${el.dataset.id}?start=${curStart}&end=${curEnd}`;
      }));
    }, 200);
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.gamesearch-wrap')) results.style.display = 'none';
  });

  const [s, e] = presetRange(7);
  loadRange(s, e);
})();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


GAME_PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>GameOS - Game</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family: system-ui, sans-serif; background:#0f1115; color:#e6e6e6; }
  header { padding:16px 24px; border-bottom:1px solid #23262e; display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
  header h1 { margin:0; font-size:20px; } header span { color:#8a8f98; font-size:13px; }
  header a { color:#60a5fa; text-decoration:none; font-size:13px; }
  .wrap { padding:20px 24px; max-width:1100px; margin:0 auto; }
  .toolbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:16px; }
  .toolbar button { background:#161a22; border:1px solid #23262e; color:#e6e6e6; border-radius:8px;
    padding:6px 14px; cursor:pointer; font-size:13px; }
  .toolbar button.active { background:#1d4ed8; border-color:#1d4ed8; color:#fff; }
  .toolbar input[type=date] { background:#161a22; border:1px solid #23262e; color:#e6e6e6;
    border-radius:8px; padding:5px 8px; font-size:13px; }
  .toolbar .sep { color:#8a8f98; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }
  .card { background:#161a22; border:1px solid #23262e; border-radius:10px; padding:14px 16px; }
  .card .k { color:#8a8f98; font-size:12px; text-transform:uppercase; letter-spacing:.05em; }
  .card .v { font-size:22px; font-weight:600; margin-top:4px; }
  .pos { color:#4ade80; } .neg { color:#f87171; }
  .panel { background:#161a22; border:1px solid #23262e; border-radius:10px; padding:16px; margin-top:16px; }
  .panel h2 { margin:0 0 10px; font-size:14px; color:#8a8f98; font-weight:600; text-transform:uppercase; letter-spacing:.05em; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  @media (max-width:800px) { .grid2 { grid-template-columns:1fr; } }
  .note { background:#1c2230; border:1px solid #2b3448; color:#9db4dd; border-radius:10px;
    padding:12px 16px; margin-top:16px; font-size:13px; }
  canvas { max-height:260px; }
  .muted { color:#8a8f98; font-size:12px; }
  body { background: radial-gradient(1200px 500px at 15% -10%, #1a2740 0%, #0f1115 55%) no-repeat, #0f1115; }
  header { align-items:center; }
  #ghead-icon { width:44px; height:44px; border-radius:11px; object-fit:cover; background:#23262e; flex:none; }
  .badge { display:inline-flex; align-items:center; gap:4px; font-size:10px; font-weight:600; padding:2px 7px;
    border-radius:20px; text-transform:uppercase; vertical-align:middle; }
  .badge svg { width:11px; height:11px; }
  .badge.amazon { background:rgba(255,153,0,.16); color:#ff9900; }
  .badge.android { background:rgba(61,220,132,.15); color:#3ddc84; }
  .badge.ios { background:rgba(200,209,220,.15); color:#c8d1dc; }
</style></head><body>
<header>
  <a href="/">&larr;</a>
  <img id="ghead-icon" style="display:none">
  <div><h1 id="title">...</h1><span id="subtitle"></span></div>
</header>
<div class="wrap">
  <div class="toolbar" id="toolbar">
    <button data-days="7">7D</button>
    <button data-days="15">15D</button>
    <button data-days="30" class="active">30D</button>
    <button data-days="all">All</button>
    <span class="sep">|</span>
    <input type="date" id="from"> <span class="sep">to</span> <input type="date" id="to">
  </div>
  <div class="cards" id="cards"></div>
  <div id="metricsNote" class="note" style="display:none">
    DAU / MAU / retention / playtime abhi khali hain &mdash; analytics source (GameAnalytics ya Firebase)
    connect hote hi yeh charts khud bhar jayenge. Revenue/spend data live hai.
  </div>
  <div class="panel"><h2>Revenue vs Spend</h2><canvas id="revspend"></canvas></div>
  <div class="grid2">
    <div class="panel"><h2>DAU / MAU</h2><canvas id="users"></canvas></div>
    <div class="panel"><h2>ARPDAU / ARPMAU ($)</h2><canvas id="arpu"></canvas></div>
    <div class="panel"><h2>Retention % (D1 / D7 / D30)</h2><canvas id="retention"></canvas></div>
    <div class="panel"><h2>Avg playtime (minutes)</h2><canvas id="playtime"></canvas></div>
  </div>
  <div class="panel"><h2>Revenue by network</h2><canvas id="networks"></canvas></div>
</div>
<script>
const $ = (id) => document.getElementById(id);
const usd = (v) => '$' + Number(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
async function j(url) { return (await fetch(url)).json(); }
const gameId = window.location.pathname.split('/').pop();
const qs = new URLSearchParams(window.location.search);
let bounds = null; const charts = {};

const GRID = {color:'#23262e'}, TICKS = {color:'#8a8f98'};
function lineOpts(extra) { return Object.assign({ plugins:{legend:{labels:{color:'#e6e6e6'}}},
  spanGaps:true, scales:{ x:{ticks:Object.assign({maxTicksLimit:10},TICKS), grid:GRID},
  y:{ticks:TICKS, grid:GRID}}}, extra||{}); }

function upsert(id, type, data, options) {
  if (charts[id]) { charts[id].data = data; charts[id].update(); return; }
  charts[id] = new Chart($(id), {type, data, options});
}

async function load(start, end) {
  const d = await j(`/api/game/${gameId}?start=${start}&end=${end}`);
  if (d.error) { document.body.innerHTML = '<p style="padding:40px">' + d.error + '</p>'; return; }
  $('title').textContent = d.game.name;
  const STORE_G = {amazon:'#ff9900',android:'#3ddc84',ios:'#c8d1dc'};
  $('subtitle').innerHTML = `<span class="badge ${d.game.store}">${d.game.store}</span> <span class="muted">${d.game.package||''}</span>`;
  if (d.game.icon) { const ic = $('ghead-icon'); ic.src = d.game.icon; ic.referrerPolicy = 'no-referrer'; ic.style.display = 'block'; }
  document.title = 'GameOS - ' + d.game.name;
  $('from').value = start; $('to').value = end;
  $('metricsNote').style.display = d.has_metrics ? 'none' : 'block';

  const sum = (a) => a.reduce((x, y) => x + (y || 0), 0);
  const totRev = sum(d.revenue), totSpend = sum(d.spend);
  const roas = totSpend > 0 ? (totRev / totSpend) : null;
  const lastNonNull = (a) => { for (let i = a.length - 1; i >= 0; i--) if (a[i] !== null && a[i] !== undefined) return a[i]; return null; };
  const dau = lastNonNull(d.dau), arpdau = lastNonNull(d.arpdau);
  $('cards').innerHTML = `
    <div class="card"><div class="k">Revenue</div><div class="v">${usd(totRev)}</div></div>
    <div class="card"><div class="k">Spend</div><div class="v">${usd(totSpend)}</div></div>
    <div class="card"><div class="k">ROAS</div><div class="v ${roas===null?'':(roas>=1?'pos':'neg')}">${roas===null?'n/a':roas.toFixed(2)}</div></div>
    <div class="card"><div class="k">DAU (latest)</div><div class="v">${dau ?? '-'}</div></div>
    <div class="card"><div class="k">ARPDAU (latest)</div><div class="v">${arpdau !== null ? '$'+arpdau : '-'}</div></div>
    <div class="card"><div class="k">Dev cost</div><div class="v">${usd(d.game.dev_cost || 0)}</div></div>`;

  upsert('revspend', 'line', { labels:d.labels, datasets:[
    {label:'Revenue', data:d.revenue, borderColor:'#4ade80', backgroundColor:'transparent', tension:.25},
    {label:'Spend', data:d.spend, borderColor:'#f87171', backgroundColor:'transparent', tension:.25}]},
    lineOpts());
  upsert('users', 'line', { labels:d.labels, datasets:[
    {label:'DAU', data:d.dau, borderColor:'#60a5fa', backgroundColor:'transparent', tension:.25},
    {label:'MAU', data:d.mau, borderColor:'#c084fc', backgroundColor:'transparent', tension:.25}]},
    lineOpts());
  upsert('arpu', 'line', { labels:d.labels, datasets:[
    {label:'ARPDAU', data:d.arpdau, borderColor:'#fbbf24', backgroundColor:'transparent', tension:.25},
    {label:'ARPMAU', data:d.arpmau, borderColor:'#f472b6', backgroundColor:'transparent', tension:.25}]},
    lineOpts());
  upsert('retention', 'line', { labels:d.labels, datasets:[
    {label:'D1', data:d.retention.d1, borderColor:'#4ade80', backgroundColor:'transparent', tension:.25},
    {label:'D7', data:d.retention.d7, borderColor:'#60a5fa', backgroundColor:'transparent', tension:.25},
    {label:'D30', data:d.retention.d30, borderColor:'#c084fc', backgroundColor:'transparent', tension:.25}]},
    lineOpts());
  upsert('playtime', 'line', { labels:d.labels, datasets:[
    {label:'Avg playtime (min)', data:d.avg_playtime_min, borderColor:'#34d399', backgroundColor:'transparent', tension:.25}]},
    lineOpts());
  upsert('networks', 'bar', { labels:d.networks.labels, datasets:[
    {label:'Revenue', data:d.networks.revenue, backgroundColor:'#60a5fa'}]},
    { indexAxis:'y', plugins:{legend:{display:false}},
      scales:{ x:{ticks:TICKS, grid:GRID}, y:{ticks:TICKS, grid:{display:false}} } });
}

function presetRange(days) {
  const end = bounds.max;
  if (days === 'all') return [bounds.min, end];
  const e = new Date(end + 'T00:00:00Z');
  const s = new Date(e); s.setUTCDate(e.getUTCDate() - (days - 1));
  const minD = new Date(bounds.min + 'T00:00:00Z');
  return [(s < minD ? minD : s).toISOString().slice(0,10), end];
}

(async () => {
  bounds = await j('/api/range');
  $('from').min = bounds.min; $('from').max = bounds.max;
  $('to').min = bounds.min; $('to').max = bounds.max;
  document.querySelectorAll('#toolbar button').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('#toolbar button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const v = btn.dataset.days;
    const [s, e] = presetRange(v === 'all' ? 'all' : parseInt(v));
    load(s, e);
  }));
  const onDate = () => { if ($('from').value && $('to').value) {
    document.querySelectorAll('#toolbar button').forEach(b => b.classList.remove('active'));
    load($('from').value, $('to').value); } };
  $('from').addEventListener('change', onDate); $('to').addEventListener('change', onDate);
  const start = qs.get('start'), end = qs.get('end');
  if (start && end) load(start, end); else { const [s, e] = presetRange(30); load(s, e); }
})();
</script></body></html>"""


@app.get("/game/{game_id}", response_class=HTMLResponse)
def game_page(game_id: int):
    return GAME_PAGE
