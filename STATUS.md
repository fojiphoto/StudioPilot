# STATUS

## 2026-07-15 — Session 1 (part 2: Phase 1 started)

### Done
- **AppLovin MAX connector live and verified with real data**: 3,560 revenue rows / ~245 games (mostly Amazon Appstore) pulled and stored. Self-test: `gameos test applovin_max`. Rolling 3-day window, idempotent replace, per-source freshness recorded.
- Models: added `AdRevenueRecord.source` (applovin_max | admob) and `Game.package_name`.
- `scripts/google_oauth.py` — one-time browser OAuth helper to obtain ADMOB/GOOGLE_ADS refresh tokens locally (no passwords shared).
- httpx request logging silenced (was printing API keys in URLs).
- Real `APPLOVIN_REPORT_KEY` lives in local `.env` (gitignored).

### Done (part 3)
- **AdMob connector live and verified**: OAuth flow completed via `scripts/google_oauth.py admob` (refresh token in `.env`), publisher auto-detected (`pub-5758568476782265`). Real pull works — only 1 row / $0.02 in last 3 days (AdMob mediation barely used; AppLovin MAX is the main mediation). Phase 1 complete.

### Done (part 4 — Phase 2 mostly complete)
- **Meta Ads connector** built + API-verified (auth, insights query, pagination OK). BUT: both ad accounts visible to the owner's token (`act_657944574693908` GamesAds USD, `act_324156006` ARS) contain **zero campaigns ever** — owner's real Meta campaigns are elsewhere (likely a Business Manager account). Owner says Meta is for future use; connector will pick data up automatically once campaigns run in a visible account. Token is short-lived — need App ID + App Secret from owner to exchange for a 60-day token.
- **Mintegral connector live and verified with real data**: 7 campaign-day rows, 1 campaign (id 171784), $0 spend last 7d (campaigns currently paused; owner confirmed Mintegral is the active UA platform). API quirks documented in the module: md5(api_key + md5(timestamp)) token, async 201→poll→type=2 TSV fetch, 7-day max window, D-1 freshness.
- `CampaignRecord.game_id` now nullable — campaigns arrive unmapped; campaign→game mapping step needed later (Mintegral Campaign dimension gives ids only, no names).

### Next
- Google Ads connector: waiting on owner for Developer Token (API Center in an MCC) + Customer ID; OAuth reuses `scripts/google_oauth.py googleads`.
- Meta long-lived token: waiting on App ID + App Secret.
- Then Phase 3 (Firebase/GameAnalytics) or jump to Phase 4 analyzers (ROAS/P&L) — revenue + spend data already flowing, analyzers are now buildable.
- Note: local SQLite dev DB reset twice for schema changes (create_all doesn't migrate). Consider Alembic once schema stabilizes.

## 2026-07-15 — Session 1

### Done
- Repo cloned/initialized from `https://github.com/fojiphoto/StudioPilot.git`.
- SPEC updated to **v2 — GameOS architecture**: headless always-on modular agent (NOT a web app). Runtime modes: continuous / interval (N minutes) / oneshot. Outputs: Telegram/WhatsApp alerts (primary) + optional read-only dashboard module. Deployment: local Windows service first, Docker/VPS later.
- `docs/SPEC.md`, `CLAUDE.md`, `STATUS.md`, `.gitignore`, `.env.example`, `README.md` committed.
- Phase 0 kernel scaffold: `gameos/` package — config loader, module registry, scheduler (3 modes), SQLAlchemy models, CLI (`gameos run/test/report`), dummy heartbeat module proving the plugin system.

### Next
- Phase 1: AppLovin MAX + AdMob connector modules (needs API credentials from owner → `.env`).

### Open questions / blockers
- API credentials per platform (SPEC §8.2) — required before Phase 1 self-tests can run against real data.
- Telegram bot token + chat id (SPEC §8.3) — needed by Phase 5.
