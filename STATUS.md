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

### Done (part 6 — Google/AdMob redo on correct account)
- Owner discovered the first AdMob OAuth was done against the WRONG Google account (that's why it showed $0.02/1 app). Redid setup fresh in the owner's own project **cross-box-games** (project number 301924103303, account crossboxgames00@gmail.com): new Desktop OAuth client, consent screen, test user.
- **AdMob now on the correct account**: publisher `pub-8035849541939283`, 420 rows / 12 apps / ~$4.70 per 3 days, verified + ingested. Both ADMOB_* and GOOGLE_ADS_* in `.env` now use the cross-box-games client. Old Irani-Gangster-project client is obsolete (its AdMob refresh token was for the wrong account).
- **Google Ads OAuth done** (adwords scope refresh token in `.env`). `google_ads` connector built: REST searchStream + GAQL, version auto-negotiation (v21→v18), micros normalization, DEVELOPER_TOKEN_NOT_APPROVED explained in self-test.
- Google Ads API Basic Access application: form filled + design doc PDF generated at `docs/GameOS-GoogleAds-API-Design.pdf` (LOCAL ONLY, gitignored — contains business figures). NOTE: form's Q2 was answered with old project number 630236845603; actual project is now 301924103303 — update Google if review asks.
- Blocker for google_ads self-test: **Google Ads API not enabled in project 301924103303** — owner given direct enable link. After that, expect DEVELOPER_TOKEN_NOT_APPROVED until Basic Access approves (~5 business days).

### Done (part 7 — backfill + mapping infrastructure)
- Google Ads API enabled in project 301924103303; `gameos test google_ads` now returns the expected "developer token not approved" (= wiring verified; waiting on Basic Access, ~5 business days).
- **Backfill implemented** (`gameos backfill <module> --days 45`): Module.backfill contract; MAX capped at its 45-day window; Mintegral walks 7-day chunks; AdMob straight range.
- **45 days ingested**: MAX 59,088 rows + AdMob 7,671 rows + Mintegral 45 campaign-day rows. Portfolio: **$2,262.21 revenue / 45d across 471 games (~$50/day)**. Mintegral spend last 45d only $0.34 (campaigns effectively idle; Google Ads history — $354 lifetime — will arrive once Basic Access approves).
- **Campaign→game mapping**: `CampaignMap` table; UA connectors (meta/mintegral/google) consult it on every pull; CLI: `gameos games [search]`, `gameos campaigns`, `gameos map <platform> <campaign_id> <game_id>` (updates existing rows too). Mintegral campaign 171784 ($0.34) still unmapped — need owner to say which game it is.
- ADMOB_PUBLISHER_ID pinned to pub-8035849541939283.

### Done (part 8 — Mintegral auto-mapping)
- Mintegral open API campaign list (`/api/open/v1/campaign`) gives campaign_name + bundle_id. Connector now auto-maps every campaign to its game by bundle_id == Game.package_name on each run (owner `gameos map` overrides win), and enriches CampaignRecord.campaign_name. **All 18 campaigns auto-mapped, 100% match.** Only campaign 171784 (TapAreena) had any delivery in the last 45d ($0.34).

### Done (part 9 — Phase 5 outputs + dashboard)
- **Telegram DROPPED** (banned in Pakistan, owner would need a VPN). Replaced with **WhatsApp Cloud API**: `whatsapp_alerts` output module delivers undelivered Alert rows, marks `delivered_via`, retries on failure. Waiting on owner: add WhatsApp product to their existing Meta app (1330394172615111) → send WHATSAPP_PHONE_NUMBER_ID + their number + token → fill WHATSAPP_* in `.env` → `gameos test whatsapp_alerts`.
- **Read-only web dashboard** (`gameos dashboard --port 8080`, gameos/dashboard.py): FastAPI + Chart.js; cards (7d revenue/spend/ROAS, lifetime net, game count), 30d revenue-vs-spend line, top-games bar, lifetime P&L table, alerts, per-source freshness. Verified in browser against real data. Engine does NOT depend on it (SPEC 5.9).

### Done (part 10 — WhatsApp live)
- **WhatsApp alerts verified end-to-end**: WhatsApp use case added to owner's Meta app, test number claimed (Phone Number ID 1229604573569755, WABA 998199606545076), owner's number verified as recipient, `gameos test whatsapp_alerts` delivered successfully. All WHATSAPP_* values in `.env`.
- Dashboard got date-range controls: 1D/3D/7D/15D/30D/All presets + calendar pickers bounded to actual data range (/api/range).

### Done (part 11 — Phase 3 groundwork: per-game analytics infrastructure)
- Owner confirmed: **GameAnalytics is on ALL games** (mandatory), Firebase only on Google Play/iOS builds (never Amazon — no Google products there). Plan: GA = primary metrics source, Firebase = supplement for GP/iOS.
- **GameAnalytics Metrics API is a PRO (paid, PipelineIQ) feature** — not on owner's plan. Owner asking GA sales/support for pricing. Plan B if too expensive: Firebase/BigQuery (free) for GP/iOS now; Amazon games stay revenue-only until GA is resolved.
- `GameMetricRecord.mau` column added (empty table dropped/recreated).
- **Dashboard per-game drill-down** (`/game/{id}` + `/api/game/{id}`): revenue-vs-spend, DAU/MAU, **ARPDAU/ARPMAU (computed as revenue ÷ DAU/MAU)**, retention D1/D7/D30, avg playtime, revenue-by-network — all with the same range presets/calendar. Top-games bar chart is click-through. Metric charts show a friendly note until an analytics connector lands (revenue/spend live already). Verified in browser on game 22.

### Next / reminders
- GameAnalytics pricing answer from owner → build `gameanalytics` connector (metrics.gameanalytics.com, X-API-Key) filling GameMetricRecord (dau/mau/retention/playtime + cohort LTV).
- If GA too costly → Firebase BigQuery connector first (needs: Firebase console → project settings → Integrations → BigQuery ON per project + service account JSON).
- WhatsApp token from API Setup is TEMPORARY (~24h). For permanent: Business settings → System user → generate token with whatsapp_business_messaging, or regenerate from API Setup when it expires. Consider a token-health self-check.
- 24h free-form window: owner should message the test number occasionally, or approve a template for anytime delivery.
- Google Ads Basic Access pending (~5 days) → then `gameos backfill google_ads --days 45`.
- Meta ads token expires ~2026-09-14 (60d) — auto-refresh module still todo.
- Game dedup by package_name (Wordall duplicates); Windows service setup; Dockerfile.
- Game dedup/merge: AdMob and MAX register the same game under different names (e.g. "Wordall: Daily Word Test" vs "Wordall - Daily Word Test Game") — merge by package_name where possible.
- Meta token auto-refresh module (60d expiry, exchanged 2026-07-16).
- When Google Ads Basic Access approves: `gameos test google_ads` then `gameos backfill google_ads --days 45`.
- Windows service setup (run `gameos run --mode interval` persistently) + Dockerfile for later VPS.
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
