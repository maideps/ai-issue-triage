# Sprint Contract

**Feature ID:** feat-001
**Feature Name:** FastAPI service skeleton
**Date:** 2026-10-27
**Estimated Duration:** 1 session

## What I Will Build

A FastAPI application in `backend/` with the four layer packages from `docs/ARCHITECTURE.md` (`api`, `services`, `domain`, `infrastructure`), a `GET /health` endpoint, typed settings read from `ISSUE_TRIAGE_*` environment variables, and the backend's lint, typecheck, test and build commands wired into the root npm scripts so the harness gates cover the backend.

## How I Will Verify

- [ ] Layer 1 (static): `npm run lint` runs the harness lint plus `ruff check` and `ruff format --check` (including `backend/scripts/`); `npm run typecheck` runs `mypy --strict` on the backend
- [ ] Layer 2 (runtime): `npm run test` runs the harness tests plus backend `pytest` (health endpoint, settings defaults, env override, invalid values rejected, layer packages importable, layer import rules)
- [ ] Layer 3 (system): `npm run check` passes end to end; `npm run verify-feature -- feat-001` passes

## What Success Looks Like

`GET /health` returns 200 with `{"status": "ok", "version": ..., "environment": ...}` in a test; settings come from `ISSUE_TRIAGE_*` environment variables and reject invalid values; a test fails if a lower layer imports a higher one; a fresh clone can run `npm run setup:backend` then `npm run check`; CI installs Python and the backend before running the harness check.

## Dependencies

- [x] Depends on: none
- [ ] Blocked by: none

## Risks

- `uv` is not installed on the author's machine; the backend uses a plain venv through a small Node launcher, `tools/py.mjs`, as ai-invoice-intake does (D-006).
- The local interpreter is Python 3.14 while CI uses 3.12; the project targets `>=3.11` and mypy/ruff are pinned to 3.11 semantics.
- The target-analysis scripts in `backend/scripts/` become subject to ruff and may need small fixes.

## Commit Plan

One commit: backend skeleton, launcher, npm script wiring, CI, docs and harness state together.

---

_Signed off by: Claude (Opus 5.5) on 2026-10-27_
