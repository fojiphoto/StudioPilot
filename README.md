# StudioPilot GameOS

An always-on, headless, **modular agent** for mobile game UA & monetization decisions: it pulls data from ad mediation (AppLovin MAX, AdMob), UA platforms (Google Ads, Meta, Mintegral) and analytics (Firebase/BigQuery, GameAnalytics), analyzes ROAS / P&L / budget pacing, and pushes decisions and alerts — without any UI needing to be open.

**Not a web app.** The engine is a daemon that runs anywhere (local machine, VPS, Docker) in one of three modes:

- `continuous` — runs 24/7
- `interval` — wakes every N minutes, runs a cycle, goes back to standby
- `oneshot` — runs one cycle and exits

See [docs/SPEC.md](docs/SPEC.md) for the full spec (source of truth) and [STATUS.md](STATUS.md) for current progress.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e .
copy .env.example .env          # fill in credentials
gameos run --mode oneshot       # one full cycle
gameos run --mode interval --every 10m
gameos test <module>            # self-test a connector
gameos report                   # current status / P&L / suggestions
```

## Layout

```
gameos/
  kernel/       config · module registry · scheduler · DB layer · event bus
  modules/
    connectors/ applovin_max · admob · google_ads · meta · mintegral · firebase_bq · gameanalytics
    analyzers/  roas · pnl · pacing · gap_checker · suggestions · preboost · store_rules
    outputs/    telegram · cli_report · dashboard (optional, read-only)
  cli.py
```
