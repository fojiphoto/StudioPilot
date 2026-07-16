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
    AdRevenueRecord, Alert, CampaignRecord, Game, PnLSnapshot, SourceSync,
)

app = FastAPI(title="GameOS Dashboard", docs_url=None, redoc_url=None)
_session_factory = None


def session_factory():
    global _session_factory
    if _session_factory is None:
        settings = load_settings()
        _session_factory = db.make_session_factory(db.make_engine(settings.db_url))
    return _session_factory


@app.get("/api/daily")
def daily(days: int = 30):
    since = date.today() - timedelta(days=days - 1)
    with session_factory()() as s:
        revenue = dict(
            s.query(AdRevenueRecord.date, func.sum(AdRevenueRecord.revenue))
            .filter(AdRevenueRecord.date >= since).group_by(AdRevenueRecord.date).all()
        )
        spend = dict(
            s.query(CampaignRecord.date, func.sum(CampaignRecord.spend))
            .filter(CampaignRecord.date >= since).group_by(CampaignRecord.date).all()
        )
    days_list = [since + timedelta(days=i) for i in range((date.today() - since).days + 1)]
    return {
        "labels": [d.isoformat() for d in days_list],
        "revenue": [round(float(revenue.get(d, 0) or 0), 2) for d in days_list],
        "spend": [round(float(spend.get(d, 0) or 0), 2) for d in days_list],
    }


@app.get("/api/top-games")
def top_games(days: int = 30, limit: int = 12):
    since = date.today() - timedelta(days=days - 1)
    with session_factory()() as s:
        rows = (
            s.query(Game.name, Game.store, func.sum(AdRevenueRecord.revenue).label("rev"))
            .join(Game, Game.id == AdRevenueRecord.game_id)
            .filter(AdRevenueRecord.date >= since)
            .group_by(Game.id).order_by(func.sum(AdRevenueRecord.revenue).desc())
            .limit(limit).all()
        )
    return {
        "labels": [f"{name[:26]} [{store}]" for name, store, _ in rows],
        "revenue": [round(float(rev or 0), 2) for _, _, rev in rows],
    }


@app.get("/api/summary")
def summary():
    since7 = date.today() - timedelta(days=6)
    with session_factory()() as s:
        rev7 = s.query(func.coalesce(func.sum(AdRevenueRecord.revenue), 0.0)).filter(
            AdRevenueRecord.date >= since7).scalar()
        spend7 = s.query(func.coalesce(func.sum(CampaignRecord.spend), 0.0)).filter(
            CampaignRecord.date >= since7).scalar()
        totals = s.query(
            func.coalesce(func.sum(PnLSnapshot.ad_revenue), 0.0),
            func.coalesce(func.sum(PnLSnapshot.spend), 0.0),
            func.coalesce(func.sum(PnLSnapshot.net), 0.0),
        ).filter(PnLSnapshot.period == "lifetime").one()
        games_count = s.query(func.count(Game.id)).scalar()
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
    roas7 = (rev7 / spend7) if spend7 else None
    return {
        "revenue_7d": round(rev7, 2), "spend_7d": round(spend7, 2),
        "roas_7d": round(roas7, 2) if roas7 is not None else None,
        "lifetime": {"revenue": round(totals[0], 2), "spend": round(totals[1], 2), "net": round(totals[2], 2)},
        "games": games_count, "sources": syncs, "alerts": alerts,
    }


@app.get("/api/pnl")
def pnl(limit: int = 15):
    with session_factory()() as s:
        rows = (
            s.query(PnLSnapshot, Game.name, Game.store)
            .join(Game, Game.id == PnLSnapshot.game_id)
            .filter(PnLSnapshot.period == "lifetime")
            .order_by(PnLSnapshot.net.desc()).limit(limit).all()
        )
    return [
        {"game": name, "store": store, "revenue": round(p.ad_revenue, 2),
         "spend": round(p.spend, 2), "dev_cost": round(p.dev_cost, 2), "net": round(p.net, 2)}
        for p, name, store in rows
    ]


PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>GameOS</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family: system-ui, sans-serif; background:#0f1115; color:#e6e6e6; }
  header { padding:16px 24px; border-bottom:1px solid #23262e; display:flex; align-items:baseline; gap:12px; }
  header h1 { margin:0; font-size:20px; } header span { color:#8a8f98; font-size:13px; }
  .wrap { padding:20px 24px; max-width:1100px; margin:0 auto; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; }
  .card { background:#161a22; border:1px solid #23262e; border-radius:10px; padding:14px 16px; }
  .card .k { color:#8a8f98; font-size:12px; text-transform:uppercase; letter-spacing:.05em; }
  .card .v { font-size:24px; font-weight:600; margin-top:4px; }
  .pos { color:#4ade80; } .neg { color:#f87171; }
  .panel { background:#161a22; border:1px solid #23262e; border-radius:10px; padding:16px; margin-top:16px; }
  .panel h2 { margin:0 0 10px; font-size:14px; color:#8a8f98; font-weight:600; text-transform:uppercase; letter-spacing:.05em; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid #23262e; }
  th { color:#8a8f98; font-weight:500; } td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .muted { color:#8a8f98; font-size:12px; }
  canvas { max-height:300px; }
</style></head><body>
<header><h1>GameOS</h1><span>read-only dashboard &mdash; the engine runs headless</span></header>
<div class="wrap">
  <div class="cards" id="cards"></div>
  <div class="panel"><h2>Revenue vs Spend (30 days)</h2><canvas id="daily"></canvas></div>
  <div class="panel"><h2>Top games by revenue (30 days)</h2><canvas id="top"></canvas></div>
  <div class="panel"><h2>P&amp;L (lifetime, top by net)</h2><table id="pnl"></table>
    <div class="muted">lifetime = since GameOS started collecting (45-day backfill)</div></div>
  <div class="panel"><h2>Alerts</h2><table id="alerts"></table></div>
  <div class="panel"><h2>Source freshness</h2><table id="sources"></table></div>
</div>
<script>
const $ = (id) => document.getElementById(id);
const usd = (v) => '$' + Number(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
async function j(url) { return (await fetch(url)).json(); }
(async () => {
  const s = await j('/api/summary');
  const roas = s.roas_7d === null ? 'n/a' : s.roas_7d.toFixed(2);
  const roasCls = s.roas_7d === null ? '' : (s.roas_7d >= 1 ? 'pos' : 'neg');
  $('cards').innerHTML = `
    <div class="card"><div class="k">Revenue 7d</div><div class="v">${usd(s.revenue_7d)}</div></div>
    <div class="card"><div class="k">Spend 7d</div><div class="v">${usd(s.spend_7d)}</div></div>
    <div class="card"><div class="k">ROAS 7d</div><div class="v ${roasCls}">${roas}</div></div>
    <div class="card"><div class="k">Net (lifetime)</div><div class="v ${s.lifetime.net>=0?'pos':'neg'}">${usd(s.lifetime.net)}</div></div>
    <div class="card"><div class="k">Games</div><div class="v">${s.games}</div></div>`;
  $('sources').innerHTML = '<tr><th>Source</th><th>Last OK</th><th>Freshness</th></tr>' +
    s.sources.map(x => `<tr><td>${x.source}</td><td>${x.last ? x.last.replace('T',' ').slice(0,16) : '-'}</td><td class="muted">${x.note||''}</td></tr>`).join('');
  $('alerts').innerHTML = s.alerts.length
    ? '<tr><th>When</th><th>Severity</th><th>Module</th><th>Message</th></tr>' +
      s.alerts.map(a => `<tr><td>${a.at}</td><td class="${a.severity==='warn'||a.severity==='critical'?'neg':''}">${a.severity}</td><td>${a.module}</td><td>${a.message}</td></tr>`).join('')
    : '<tr><td class="muted">no alerts</td></tr>';

  const d = await j('/api/daily');
  new Chart($('daily'), { type:'line', data:{ labels:d.labels, datasets:[
      {label:'Revenue', data:d.revenue, borderColor:'#4ade80', backgroundColor:'transparent', tension:.25},
      {label:'Spend', data:d.spend, borderColor:'#f87171', backgroundColor:'transparent', tension:.25}]},
    options:{ plugins:{legend:{labels:{color:'#e6e6e6'}}}, scales:{
      x:{ticks:{color:'#8a8f98', maxTicksLimit:10}, grid:{color:'#23262e'}},
      y:{ticks:{color:'#8a8f98'}, grid:{color:'#23262e'}}}}});

  const t = await j('/api/top-games');
  new Chart($('top'), { type:'bar', data:{ labels:t.labels, datasets:[
      {label:'Revenue', data:t.revenue, backgroundColor:'#60a5fa'}]},
    options:{ indexAxis:'y', plugins:{legend:{display:false}}, scales:{
      x:{ticks:{color:'#8a8f98'}, grid:{color:'#23262e'}},
      y:{ticks:{color:'#8a8f98'}, grid:{display:false}}}}});

  const p = await j('/api/pnl');
  $('pnl').innerHTML = '<tr><th>Game</th><th>Store</th><th class="num">Revenue</th><th class="num">Spend</th><th class="num">Dev cost</th><th class="num">Net</th></tr>' +
    p.map(r => `<tr><td>${r.game}</td><td>${r.store}</td><td class="num">${usd(r.revenue)}</td><td class="num">${usd(r.spend)}</td><td class="num">${usd(r.dev_cost)}</td><td class="num ${r.net>=0?'pos':'neg'}">${usd(r.net)}</td></tr>`).join('');
})();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE
