# StudioPilot GameOS — UA & Monetization Agent — Project Spec (v2)

Owner: Nawaz (game producer/publisher — iOS & Android)
Purpose: This document is the source of truth for Claude Code to build the project. It lives at `docs/SPEC.md` and must be read at the start of every session.

> **v2 change (2026-07-15):** The project is **NOT a web application**. It is **GameOS** — an always-on, headless, modular agent (a daemon/service) that runs anywhere (local machine, remote server, cloud, Docker) and keeps working on its own. Nothing starts "when you open a web page." A web dashboard is only an *optional read-only module* on top; the engine never depends on it.

---

## 1. Background

Solo/small-team mobile game publisher. Games are published on iOS and Android (Amazon Appstore also in scope). Monetization via ad mediation: **AppLovin MAX** and **AdMob**. User acquisition (UA) campaigns run on **Google Ads**, **Meta**, and **Mintegral**.

Current pain points:
- Budget is small (~$150/game), so campaigns are still in a learning phase.
- Campaign setup/targeting is often not optimal.
- CPI is high (minimum ~$1, often more) on Meta and Mintegral.
- Budget pacing is inconsistent — sometimes doesn't spend, sometimes all of it gets eaten by one app/ad set.
- No centralized way to see whether a campaign is actually profitable (ROAS) until after the fact.
- Currently operating at a loss overall while learning.

## 2. Goal

Build **GameOS**: a self-running agent that connects to all ad/UA/analytics platforms, continuously (or on a configurable schedule) pulls data, analyzes it, and tells the owner: *should we spend on this game, how much, and is it working* — without anyone having to open anything.

## 3. Architecture — GameOS Core

### 3.1 Shape

```
+--------------------------------------------------------------+
|                        GameOS Kernel                         |
|  config · scheduler · module registry · event bus · DB layer |
+--------------------------------------------------------------+
        |                    |                     |
   [Connectors]         [Analyzers]           [Outputs]
   applovin_max         roas_engine           telegram_alerts
   admob                pnl_engine            whatsapp_alerts
   google_ads           pacing_monitor        dashboard (optional,
   meta                 gap_checker            read-only web view)
   mintegral            suggestion_engine     cli_report
   firebase_bq          preboost_advisor
   gameanalytics        store_rules
```

- **Kernel** — the only "core" code. Loads config, discovers/registers modules, runs the scheduler, owns the DB connection, routes events (e.g. "new data pulled" → analyzers run → alerts fire).
- **Modules** — everything else. Each module is a self-contained plugin (one folder/file) implementing a small interface (`setup()`, `run(context)`, `teardown()`, plus metadata: name, type, schedule hint). Dropping a new module in and listing it in config is all that's needed — **adding a connector/analyzer/output must never require touching kernel code. Hard requirement.**

### 3.2 Runtime modes (owner-controlled, changeable at any time)

| Mode | Behavior |
|---|---|
| `continuous` | Runs 24/7; each module fires on its own cadence (per-platform pull intervals, analyzers after pulls). |
| `interval` | Wakes every N minutes (N configurable: 2, 10, 60 …), runs a full cycle (pull → analyze → alert), goes back to standby. |
| `oneshot` | Runs one full cycle and exits. Useful for manual runs and cron/Task Scheduler-driven setups. |

Mode and interval live in config (`gameos.yaml` / env) and can also be changed via CLI (`gameos run --mode interval --every 10m`) without code changes.

### 3.3 Deployment

- **Stage 1 (now):** run locally on the owner's Windows machine as a background service (Task Scheduler / NSSM / `pythonw`), zero cost.
- **Stage 2 (later):** the exact same code in Docker on a cheap VPS for true 24/7 uptime. Nothing in the code may assume Windows or assume localhost.

### 3.4 Stack

- **Core:** Python 3.11+ · APScheduler (async) for scheduling · SQLAlchemy for DB · Pydantic for config/schemas.
- **DB:** SQLite by default (zero-setup local), PostgreSQL via a single config switch for cloud. Same models/migrations (Alembic) for both.
- **Outputs:** Telegram Bot API (primary alerts; WhatsApp later via provider), optional lightweight dashboard module (FastAPI serving a small read-only page — it only *reads* the DB; engine runs fine with it disabled).
- **Secrets:** `.env` (gitignored); `.env.example` documents required keys.

## 4. Data Sources (confirmed to have programmatic access)

| Platform | Data | API | Refresh cadence |
|---|---|---|---|
| AppLovin MAX | Ad impressions/revenue/eCPM by app/country/network | Revenue Reporting API | Near real-time (up to 45-day query window) |
| AdMob | Mediation revenue/impressions | Mediation Report API (`accounts.mediationReport.generate`) | Near real-time |
| Google Ads | UA campaign spend/installs/CPI | Google Ads API (GAQL) | Near real-time |
| Meta | UA campaign spend/installs/CPI | Marketing/Ads Insights API | Near real-time (70+ metrics available) |
| Mintegral | UA campaign spend/installs/CPI | Reporting API | **Delayed** — data lands ~1.5h after day end; recommended pull time is 1:30am next day. Treat as D-1, not real-time. |
| Firebase | Playtime, country, retention, user_ltv | BigQuery export (daily sync; ~48h initial setup) | Daily |
| GameAnalytics | Retention, playtime, cohorts, raw events | Data Export (real-time raw stream to S3/GCS) + Metrics API | Real-time |

Important: never label everything "real-time". Mintegral and Firebase are inherently a day behind — every stored record and every report/alert carries a per-source "last updated" timestamp.

## 5. Feature List (all implemented as modules)

### 5.1 Connector modules (type: `connector`)
- One per platform above. Each: auth, scheduled pull (interval appropriate to that platform's cadence), normalize into shared schema, write to DB.
- Each connector ships with a self-test (`gameos test applovin_max`) confirming it can auth and return real data.

### 5.2 Unified data warehouse
- SQLite/Postgres holding normalized **historical** records (never just latest snapshot) for ads revenue, campaigns, and game metrics per game/date/country/platform — cohort and LTV analysis need history.

### 5.3 Pre-Launch / Pre-Boost Advisor (type: `analyzer`)
- Before starting a UA campaign on a game: **should we boost this game, and with what budget?**
- Logic: compare the game's early retention/playtime/monetization signals against the owner's own historical portfolio benchmarks — projected LTV vs. likely CPI on that platform/store.
- Output: go / no-go + suggested starting budget + which platform to start on.

### 5.4 Campaign Health Monitor (type: `analyzer`)
- Budget pacing alerts: under-spending, or one campaign/ad set consuming the whole budget.
- ROAS positive/negative flag per campaign, refreshed every cycle.
- Ongoing suggestions ("pause this ad set — 80% of budget, zero positive ROAS", "this campaign is trending positive, consider raising budget").

### 5.5 Profit & Loss Engine (type: `analyzer`)
- Inputs: ad revenue + IAP revenue, ad spend, **dev cost** (manual entry per game).
- Output: true net P&L per game and portfolio-wide — the full picture including what the game cost to build.

### 5.6 Platform/Store-Specific Strategy Rules (type: `analyzer`)
- Store economics differ (e.g. Amazon Appstore: low volume, noticeably higher eCPM). Per-store benchmark data (typical eCPM, volume, CPI) drives store-specific recommendations — never one generic rule for all stores. Ruleset must be easy to extend.

### 5.7 Analytics Gap Checker (type: `analyzer`)
- Scans events actually implemented in Firebase/GameAnalytics per game against a standard checklist: session start/end, level start/fail/complete, `ad_impression` per placement, purchase/IAP, retention D1/D7/D30, LTV, funnel steps.
- Flags gaps as **Major** (blocks a real decision) or **Minor** (nice-to-have), and states *what decision each gap is blocking*.

### 5.8 Suggestion Engine (type: `analyzer`)
- Always-on, rule-based recommendations (smarter heuristics later): budget changes, pause/scale calls, platform switches, instrumentation gaps to fix first. Feeds the output modules.

### 5.9 Output modules (type: `output`)
- **telegram_alerts** (primary): pushes alerts/suggestions/daily digest to the owner's phone. Severity levels so 2-minute cycles don't spam.
- **dashboard** (optional): small read-only web view over the DB — portfolio overview, per-game drill-down, trend graphs, P&L, alerts. Engine never depends on it.
- **cli_report**: `gameos report [--game X]` prints current status/P&L/suggestions in the terminal; also writes daily report files.

### 5.10 Extensibility (hard requirement)
- Plugin-style module structure as in 3.1. New ad networks, UA platforms, analyzers, or outputs bolt on at any time without touching the kernel.

## 6. Core Data Model (high level)

- `Game` — id, name, store (ios/android/amazon), genre, launch_date, dev_cost
- `AdRevenueRecord` — game_id, date, hour, network, country, platform, impressions, ecpm, revenue
- `CampaignRecord` — game_id, date, ua_platform (google/meta/mintegral), campaign_id, spend, installs, cpi, clicks, impressions
- `GameMetricRecord` — game_id, date, country, dau, avg_playtime, retention_d1/d7/d30, sessions
- `CohortLTV` — game_id, cohort_date, day_n, cumulative_ltv
- `AnalyticsGap` — game_id, event_name, severity (major/minor), status, decision_blocked_note
- `PnLSnapshot` — game_id, period, ad_revenue, iap_revenue, spend, dev_cost, net
- `SourceSync` — source, last_success_at, freshness_note (drives "last updated" per source)
- `Alert` — created_at, severity, module, game_id, message, delivered_via, acknowledged

## 7. Development Plan — Session & Token Aware

### Why this section exists
Claude Code runs in a **5-hour session cap and a weekly usage cap**, and saves work **locally, not automatically to a remote**. Work happens from two locations (office/home), so the *only* thing that survives between sessions/locations is what's committed and pushed to git. Phases are cut small enough to fit one sitting and each ends with a push.

### Repo
- Remote: `https://github.com/fojiphoto/StudioPilot.git` (confirmed).
- Root contains: `docs/SPEC.md` (this file), `CLAUDE.md` (session instructions), `STATUS.md` (running log — done / next / blockers, updated at the end of every session), `.env.example`.

### 7.1 Phase breakdown

**Day 1**
- Phase 0 — Kernel scaffold: repo files, folder structure, kernel (config loader, module registry, scheduler with 3 runtime modes, DB layer + models + Alembic), CLI entrypoint (`gameos run/test/report`), one dummy module proving the plugin system works end-to-end. Commit + push.
- Phase 1 — AppLovin MAX + AdMob connector modules (core monetization data). Self-tests confirm real pulls. Commit + push.
- Phase 2 — Google Ads + Meta + Mintegral connector modules → `CampaignRecord`. Commit + push.

**Day 2**
- Phase 3 — Firebase (BigQuery) + GameAnalytics connectors, cohort/LTV computation. Commit + push.
- Phase 4 — Analyzer modules: ROAS engine, P&L engine, pacing monitor, gap checker. Commit + push.
- Phase 5 — Output modules: Telegram alerts + CLI report; wire severity levels and daily digest. Commit + push.
- Phase 6 (buffer/stretch) — Pre-boost advisor, per-store strategy rules, optional read-only dashboard module, Windows service setup, Docker file for later cloud deploy.

Each phase ≈ one session. End of every phase: working code committed, `STATUS.md` updated, pushed. If a phase runs long, stop at a clean sub-point, commit, note the exact resume point in `STATUS.md`.

### 7.2 Git workflow
- Single `main` branch; commit at every safe checkpoint so a killed/expired session never loses work.
- `git pull` at session start, `git push` before session end.
- Never commit secrets — only `.env.example`.

### 7.3 Instructions for Claude Code (mirrored in `CLAUDE.md`)
- Session budget is limited — work in the smallest safe increment; commit and push *before* the budget runs out.
- Update `STATUS.md` at the end of every session: done / next / open questions.
- Never invent API credentials — ask the user; real values only in `.env`.
- Prioritize correctness of P&L math, ROAS math, and data normalization above everything cosmetic.
- Modules must stay isolated/pluggable; kernel stays closed to feature edits (hard requirement).
- Every connector gets a self-test verifying it returns real data before moving on.
- The engine must never require a browser, a web page, or any UI to be open in order to work.

## 8. Open Items Needing Input Before Dev Starts

1. ~~Git remote~~ — **done:** `https://github.com/fojiphoto/StudioPilot.git`.
2. **API access status** per platform — Meta `ads_read` approved, AdMob API access granted, Mintegral advertiser API key issued, Google Ads developer token approved, Firebase project linked to BigQuery. Needed to build/test real connectors (Phases 1–3).
3. **Telegram bot** — create a bot via @BotFather and provide the token + chat id (needed in Phase 5).
