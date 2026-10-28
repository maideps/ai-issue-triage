# Architecture

## Layer Model

This project follows a layered structure:

```
┌─────────────────────────────────────────────┐
│               Presentation Layer             │
│  (UI, CLI, API endpoints — user-facing)     │
├─────────────────────────────────────────────┤
│               Application Layer              │
│  (Use cases, orchestration, workflows)       │
├─────────────────────────────────────────────┤
│                 Domain Layer                  │
│  (Business logic, entities, rules)           │
├─────────────────────────────────────────────┤
│             Infrastructure Layer             │
│  (Persistence, networking, external APIs)    │
└─────────────────────────────────────────────┘
```

## Harness Boundaries

The harness contract (described in `.harness/manifest.json` and enforced by `make check-arch` / `npm run check-arch`) separates:

- **CORE** — files that ship as-is and must not be edited (runner, scripts, templates, root operating manual).
- **TEMPLATES** — adopter skeletons under `templates/`.
- **INSTANCE** — this project's own state (feature list, progress, decisions, docs).
- **PRODUCT-OWNED** — directories listed in the manifest's `productRoots`.

Verification entrypoints delegate to the Node runner (`scripts/framework-check.mjs`); Makefile targets and npm scripts are 1:1 mirrors.

## Worktree Lanes

Multiple agents can work concurrently, each in its own sibling git worktree on branch `lane/<feature-id>` (`make lane-new F=<id>`, `lane-list`, `lane-land F=<id>`, `lane-drop F=<id>`). A branch can only be checked out in one worktree at a time, so git's own branch-checkout exclusivity is the coordination primitive — no lock file needed. WIP=1 is enforced per lane: a `lane/<feature-id>` branch may only activate the feature it is named for.

## Sharded Feature State

`feature_list.json` declares what each feature is and rarely changes; each feature's mutable state (`state`, `evidence`) lives in its own file, `.harness/state/<feature-id>.json`. `verify-feature` writes only that shard, never the shared declarations file — two lanes finishing two different features merge without conflict. `npm run migrate-feature-state` moves an older repo's inline state into shards.

## Application Dependency Rules

- Layers may only depend on layers below them
- Domain layer has zero external dependencies
- Infrastructure implements interfaces defined by domain
- Presentation never directly accesses infrastructure

Encode these rules as entries in `.harness/arch-rules.json` as the codebase grows.

## Enforcement

```bash
make check-arch     # or: npm run check-arch
```

## Module Documentation

Co-locate architecture notes with the modules they affect; record framework-wide decisions in the root `DECISIONS.md`.

## Product Architecture

```
Issue source adapter            FastAPI service                         PostgreSQL + pgvector
(GitHub replay | sandbox  --->  guardrails -> LangGraph flow  <------>  issues, docs chunks, embeddings,
 webhook; later Jira...)          | triage (classifier -> LLM)           checkpoints, feedback, eval results
                                  | retrieval (hybrid + rerank)
                                  | action: duplicate | answer | needs-info | bug summary
                                  v
                          human gate (React review UI)  --->  post only to allowlisted sandbox repo
                                  |
                          LLM provider interface (LiteLLM | deterministic fake)
```

Product directories (declared in `.harness/manifest.json` as `productRoots`):

- `backend/` — FastAPI service, ingestion and evaluation code (Python). Layers: `api/` (presentation), `services/` (application: the agent flow and use cases), `domain/` (issues, labels, duplicate links, evaluation cases, rules), `infrastructure/` (database, vector search, GitHub client, LLM providers). `backend/scripts/` holds the target-analysis scripts. The layer import rules are enforced by `backend/tests/test_layers.py`. `main.py` (HTTP) and `cli.py` (`python -m issue_triage`) are composition roots above the layers: they wire infrastructure implementations into services. Ingested issues live in `data/github/<owner>__<name>/issues.jsonl` with a `state.json` resume cursor. `load` upserts them into PostgreSQL (`issues` and `comments` tables, defined in `infrastructure/db/models.py` and created only by Alembic migrations under `infrastructure/db/migrations/`; a test fails if the two drift apart).
- `tools/` — `py.mjs`, the launcher that runs the backend's Python tooling from the root npm scripts on any OS (D-006).
- `evals/` — evaluation datasets (small committed samples) and results summaries.
- `frontend/` — React + TypeScript review UI (added by feat-014).

Key design rules:

- The issue source and the LLM provider are interfaces owned by the domain; GitHub and LiteLLM implement them, and tests use fakes.
- Every evaluation case carries an as-of timestamp, and retrieval never returns items created after it (D-004).
- Agent nodes exchange typed schemas, never free text, and each node receives only the context it declares.
- Nothing is posted outside the sandbox allowlist (D-003).
