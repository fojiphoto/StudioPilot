# StudioPilot GameOS — Claude Code Session Instructions

Read `docs/SPEC.md` first — it is the source of truth. Then read `STATUS.md` to see exactly where the last session stopped.

## What this project is
GameOS: an always-on, headless, **modular agent** (daemon) for UA & monetization decisions across the owner's mobile game portfolio. It is NOT a web app — the engine must never require a browser or UI to be open. Optional read-only dashboard module only.

## Session rules
- Session budget is limited (5h/session, weekly cap): work in the smallest safe increment; commit and push *before* the budget runs out, not after.
- `git pull` at session start; `git push` before session end. Work happens from two locations — only pushed work survives.
- Update `STATUS.md` at end of every session: done / next / open questions.

## Engineering rules
- Never invent API credentials — ask the user; real values live only in `.env` (gitignored). Keep `.env.example` updated.
- Correctness of P&L math, ROAS math, and data normalization beats everything cosmetic.
- **Hard requirement:** plugin architecture. Connectors/analyzers/outputs are self-contained modules; adding one must never require kernel changes.
- Every connector ships with a self-test (`gameos test <module>`) proving it returns real data, before moving to the next phase.
- Nothing may assume Windows or localhost — the same code must run in Docker on a VPS later.
- Runtime modes (`continuous` / `interval` / `oneshot`) and the interval are config/CLI controlled — the owner may run it 24/7 or for 2 minutes at a time.
