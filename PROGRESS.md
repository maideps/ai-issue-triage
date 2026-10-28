# Progress Log

Generated current-state dashboard and index of the per-session notes in `progress/`.

After adding or updating a session note, regenerate this file with `node scripts/framework-check.mjs progress-update`.

<!-- BEGIN GENERATED PROGRESS -->
## Current Verified State

- Project root: this repository
- Primary startup command: `./init.sh`
- Primary verification command: `make check` (make-free equivalent: `npm run check`)
- Active feature: none
- Passing features (1): feat-001 — FastAPI service skeleton
- Failing/blocked features: none
- Planned features (19): feat-002 — GitHub issue ingestion, feat-003 — Issue domain model and persistence, feat-004 — Ground-truth datasets with time split, feat-005 — LLM provider layer, feat-006 — Input and output guardrails, feat-007 — LLM triage baseline and eval runner, feat-008 — Docs ingestion and chunking in pgvector, feat-009 — Hybrid duplicate search, feat-010 — Reranking and query transformation, feat-011 — Doc-grounded answer drafting, feat-012 — Agent orchestration with a human gate, feat-013 — Triage decision model, feat-014 — Review queue UI, feat-015 — Replay mode, feat-016 — Sandbox live mode, feat-017 — Observability and KPI view, feat-018 — Docker Compose and CI with evals, feat-019 — MCP server for search tools, feat-020 — README, diagram and demo
- Last evidence: feat-001 — FastAPI service skeleton — All verification layers passed via node scripts/framework-check.mjs verify-feature (SKIP reported in: Layer 2: Runtime tests, Layer 3: System confirmation) (2026-10-28T01:12:13.303Z)

## Session Index

- [Session 001 — 2026-10-27](progress/001-2026-10-27.md)
- [Session 002 — 2026-10-27](progress/002-2026-10-27.md)
- [Session 003 — 2026-10-27](progress/003-2026-10-27.md)
<!-- END GENERATED PROGRESS -->
