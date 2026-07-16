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


@app.get("/api/daily")
def daily(start: str | None = None, end: str | None = None):
    start_date, end_date = _range(start, end)
    with session_factory()() as s:
        revenue = dict(
            s.query(AdRevenueRecord.date, func.sum(AdRevenueRecord.revenue))
            .filter(AdRevenueRecord.date >= start_date, AdRevenueRecord.date <= end_date)
            .group_by(AdRevenueRecord.date).all()
        )
        spend = dict(
            s.query(CampaignRecord.date, func.sum(CampaignRecord.spend))
            .filter(CampaignRecord.date >= start_date, CampaignRecord.date <= end_date)
            .group_by(CampaignRecord.date).all()
        )
    days_list = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
    return {
        "labels": [d.isoformat() for d in days_list],
        "revenue": [round(float(revenue.get(d, 0) or 0), 2) for d in days_list],
        "spend": [round(float(spend.get(d, 0) or 0), 2) for d in days_list],
    }


@app.get("/api/top-games")
def top_games(start: str | None = None, end: str | None = None, limit: int = 12):
    start_date, end_date = _range(start, end)
    with session_factory()() as s:
        rows = (
            s.query(Game.name, Game.store, func.sum(AdRevenueRecord.revenue).label("rev"))
            .join(Game, Game.id == AdRevenueRecord.game_id)
            .filter(AdRevenueRecord.date >= start_date, AdRevenueRecord.date <= end_date)
            .group_by(Game.id).order_by(func.sum(AdRevenueRecord.revenue).desc())
            .limit(limit).all()
        )
    return {
        "labels": [f"{name[:26]} [{store}]" for name, store, _ in rows],
        "revenue": [round(float(rev or 0), 2) for _, _, rev in rows],
    }


@app.get("/api/stats")
def stats(start: str | None = None, end: str | None = None):
    """Revenue/spend/ROAS for the selected range (drives the top cards)."""
    start_date, end_date = _range(start, end)
    with session_factory()() as s:
        revenue = s.query(func.coalesce(func.sum(AdRevenueRecord.revenue), 0.0)).filter(
            AdRevenueRecord.date >= start_date, AdRevenueRecord.date <= end_date).scalar()
        spend = s.query(func.coalesce(func.sum(CampaignRecord.spend), 0.0)).filter(
            CampaignRecord.date >= start_date, CampaignRecord.date <= end_date).scalar()
    roas = (revenue / spend) if spend else None
    return {
        "days": (end_date - start_date).days + 1,
        "revenue": round(revenue, 2),
        "spend": round(spend, 2),
        "roas": round(roas, 2) if roas is not None else None,
    }


@app.get("/api/summary")
def summary():
    """Range-independent info: lifetime P&L totals, game count, sources, alerts."""
    with session_factory()() as s:
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
    return {
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
  header { padding:16px 24px; border-bottom:1px solid #23262e; display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
  header h1 { margin:0; font-size:20px; } header span { color:#8a8f98; font-size:13px; }
  .wrap { padding:20px 24px; max-width:1100px; margin:0 auto; }
  .toolbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:16px; }
  .toolbar button { background:#161a22; border:1px solid #23262e; color:#e6e6e6; border-radius:8px;
    padding:6px 14px; cursor:pointer; font-size:13px; }
  .toolbar button:hover { border-color:#60a5fa; }
  .toolbar button.active { background:#1d4ed8; border-color:#1d4ed8; color:#fff; }
  .toolbar input[type=date] { background:#161a22; border:1px solid #23262e; color:#e6e6e6;
    border-radius:8px; padding:5px 8px; font-size:13px; }
  .toolbar .sep { color:#8a8f98; }
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
  <div class="cards" id="cards"></div>
  <div class="panel"><h2 id="dailyTitle">Revenue vs Spend</h2><canvas id="daily"></canvas></div>
  <div class="panel"><h2 id="topTitle">Top games by revenue</h2><canvas id="top"></canvas></div>
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

let bounds = null, lifetime = null, dailyChart = null, topChart = null;

function setActive(btn) {
  document.querySelectorAll('#toolbar button').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add(btn ? 'active' : '');
}

async function loadRange(start, end) {
  const qs = `start=${start}&end=${end}`;
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

  if (topChart) { topChart.data.labels = t.labels; topChart.data.datasets[0].data = t.revenue; topChart.update(); }
  else topChart = new Chart($('top'), { type:'bar', data:{ labels:t.labels, datasets:[
      {label:'Revenue', data:t.revenue, backgroundColor:'#60a5fa'}]},
    options:{ indexAxis:'y', plugins:{legend:{display:false}}, scales:{
      x:{ticks:{color:'#8a8f98'}, grid:{color:'#23262e'}},
      y:{ticks:{color:'#8a8f98'}, grid:{display:false}}}}});

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
  const [b, sum] = await Promise.all([j('/api/range'), j('/api/summary')]);
  bounds = b; lifetime = { net: sum.lifetime.net, games: sum.games };
  $('from').min = b.min; $('from').max = b.max; $('to').min = b.min; $('to').max = b.max;

  $('sources').innerHTML = '<tr><th>Source</th><th>Last OK</th><th>Freshness</th></tr>' +
    sum.sources.map(x => `<tr><td>${x.source}</td><td>${x.last ? x.last.replace('T',' ').slice(0,16) : '-'}</td><td class="muted">${x.note||''}</td></tr>`).join('');
  $('alerts').innerHTML = sum.alerts.length
    ? '<tr><th>When</th><th>Severity</th><th>Module</th><th>Message</th></tr>' +
      sum.alerts.map(a => `<tr><td>${a.at}</td><td class="${a.severity==='warn'||a.severity==='critical'?'neg':''}">${a.severity}</td><td>${a.module}</td><td>${a.message}</td></tr>`).join('')
    : '<tr><td class="muted">no alerts</td></tr>';
  const p = await j('/api/pnl');
  $('pnl').innerHTML = '<tr><th>Game</th><th>Store</th><th class="num">Revenue</th><th class="num">Spend</th><th class="num">Dev cost</th><th class="num">Net</th></tr>' +
    p.map(r => `<tr><td>${r.game}</td><td>${r.store}</td><td class="num">${usd(r.revenue)}</td><td class="num">${usd(r.spend)}</td><td class="num">${usd(r.dev_cost)}</td><td class="num ${r.net>=0?'pos':'neg'}">${usd(r.net)}</td></tr>`).join('');

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

  const [s, e] = presetRange(7);
  loadRange(s, e);
})();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE
