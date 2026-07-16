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

### Done (part 5 — 2026-07-16)
- **Meta long-lived token**: exchanged via fb_exchange_token (60 days), App ID/Secret in `.env`. TODO: auto-refresh module before day ~55.
- **Google Ads**: owner created MCC + got Developer Token (`.env`), Customer ID `5024835248` (Cross Box Games). Access level is **Test Account** — owner must click "Apply for Basic Access" in API Center; until approved, production queries will fail (expected). OAuth for adwords scope FAILED with 403 access_denied: `crossboxgames00@gmail.com` is not a test user on the OAuth consent screen — owner is adding it (Google Auth Platform → Audience → Test users), then re-run `python scripts/google_oauth.py googleads`.
- **Phase 4 started — analyzers live:**
  - `roas_engine`: portfolio + per-game ROAS over rolling 7d; alerts on negative ROAS (with $5 min-spend threshold), unmapped-spend alert, 24h alert cooldown/dedupe.
  - `pnl_engine`: lifetime PnLSnapshot per game (ad_rev + IAP − spend − dev_cost); dev_cost manual on Game rows. 252 games computed, portfolio net +$145.82 (revenue side only so far; spend last 7d = $0 since Mintegral campaigns paused).
  - `gameos report` now shows: freshness, 7d portfolio ROAS, lifetime P&L top games + portfolio, alerts.
- Caveat noted in code: "lifetime" = since GameOS started collecting. Historical backfill (MAX supports 45d query window) is a pending item.

### Next
- Google Ads: after owner adds test user → OAuth → build connector (GAQL searchStream). Data flows once Basic Access approved.
- Campaign→game mapping mechanism (Mintegral campaign 171784 unmapped; CampaignRecord.game_id NULL).
- Historical backfill for AppLovin MAX (45 days) so P&L means something.
- Phase 5 outputs: Telegram alerts (needs bot token + chat id from owner).
- Note: local SQLite dev DB reset twice for schema changes. Consider Alembic once schema stabilizes.

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
