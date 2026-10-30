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
- LLM calls go through `services/llm.py` (`LLMClient`): the step name picks the model (`ISSUE_TRIAGE_LLM_MODELS`), structured output is validated against a Pydantic schema with one corrective retry, and every call lands in a `CallLedger` with tokens, cost and latency. Providers implement `domain/llm.py`'s `LLMProvider`: `infrastructure/llm/fake.py` (deterministic, `fake/<handler>` models, the default) and `litellm_provider.py` (real models); `RoutingProvider` keeps `fake/` models off the network. Prompts are versioned files under `issue_triage/prompts/<name>/<version>/`. `wiring.py` builds the client from settings for the CLI and the API.
- `tools/` — `py.mjs`, the launcher that runs the backend's Python tooling from the root npm scripts on any OS (D-006).
- `evals/` — evaluation datasets (small committed samples) and results summaries.
- `frontend/` — React + TypeScript review UI (added by feat-014).

Key design rules:

- The issue source and the LLM provider are interfaces owned by the domain; GitHub and LiteLLM implement them, and tests use fakes.
- Every evaluation case carries an as-of timestamp, and retrieval never returns items created after it (D-004).
- Agent nodes exchange typed schemas, never free text, and each node receives only the context it declares.
- Nothing is posted outside the sandbox allowlist (D-003).
- Docs retrieval: `infrastructure/docs_source.py` resolves the last commit before a date through the GitHub API and extracts only `docs/**/*.md` from its tarball into `data/repos/<owner>__<name>@<sha>/`; `domain/docs.py` holds the two chunkers; `services/docs_index.py` embeds chunks (`infrastructure/embeddings.py`, local sentence-transformers, or a hashing fake in tests) into `doc_chunks` (pgvector, HNSW cosine index, migration `0002`) and scores retrieval against maintainer-linked pages.
- Similar-issue search (`domain/search.py`, `services/duplicates.py`): issue embeddings live in `issue_embeddings` (pgvector HNSW, migration `0003`; `SET LOCAL hnsw.iterative_scan` keeps scanning until k rows pass the `created_at < as_of` filter), keyword search is in-memory BM25 (`infrastructure/keyword_index.py`, tokens keep flags, versions and env-var names), and hybrid mode fuses the two rankings with reciprocal rank fusion (k=60). Every search takes `before` and `exclude`, so a replayed issue only ever sees older issues. Optional stages: `services/query_rewrite.py` (LLM step `query_rewrite`, prompt `query_rewrite@v1`) adds up to three reformulated queries fused by RRF (on by default, D-009); `infrastructure/reranker.py` re-scores the top 20 with a cross-encoder (off by default: it lowered recall).
- Answers (`services/answers.py`, `services/answer_eval.py`): `DocRetriever` fetches the top heading chunks, `AnswerDrafter` renders `answer@v1` with each excerpt labelled by its id (`page#position`), validates citations against what was retrieved and marks drafts that cite nothing, cite invented ids, are unsure or were flagged by the guardrails as `needs_human`. `Judge` (`judge@v1`) grades a draft against the maintainer's reply; `domain/answer_metrics.py` holds the LLM-free metrics (D-010).
- Agent (`services/agent/`): `graph.py` builds the LangGraph flow from `NodeSpec`s (declared reads and writes, enforced by `scoped`), `state.py` holds the typed hand-offs, `runner.py` (`TriageAgent`) starts, resumes, reviews and inspects runs per issue thread. `infrastructure/checkpointer.py` provides LangGraph's `PostgresSaver` on the application database; its tables are created by `saver.setup()`, not Alembic, and `owned_by_alembic` keeps them out of migration comparisons. `wiring.build_agent_deps` assembles the real dependencies (D-011).
- Decision model (`services/decision_model.py`): `EmbeddingClassifier` (logistic regression over the stored issue embeddings), `escalation_curve` for threshold tuning, and `EscalatingTriager`, which the agent uses for type triage when a trained model exists in `data/models/` (D-012).
- Review queue (`services/review_queue.py`, `api/review.py`, migration `0004`): every agent proposal becomes a `triage_runs` record with its evidence; a review resumes the agent and appends to `review_feedback` (original text, final text, decision, reviewer). Routes: `GET /api/runs`, `GET|POST /api/runs/{owner}/{name}/{number}`, `POST .../review`. `main.create_app` builds the real queue lazily (`wiring.LazyReviewQueue`) and tests inject their own.
- `frontend/`: React 19 + TypeScript + Vite review UI (`QueueList`, `RunDetail`, typed `api.ts`), Vitest + Testing Library component tests; the root `typecheck`, `test` and `build` scripts include it.
- Replay (`domain/replay.py`, `services/replay_eval.py`): `maintainer_outcome` reads what happened after `as_of` (type label, duplicate link, needs-info, first maintainer reply) and `agreement` compares it with a proposal; run details carry both for the UI, and `eval-replay` measures agreement over a test-split sample. The outcome never reaches the agent (D-013).
- Live mode (`services/live.py`, `api/webhooks.py`, `domain/publishing.py`, `infrastructure/github_rest.py`): `LiveIntake` accepts issues only from allowlisted sandboxes (signed webhook or `sandbox-poll`), pseudonymizes and queues them; `SafePublisher` is the only path to GitHub comments and enforces the allowlist and the protected evaluation target; the review queue posts only after approval (D-014).
- Observability (`domain/tracing.py`, `services/tracing.py`, `services/kpis.py`, `api/kpis.py`, migration `0005`): `Tracer` wraps every graph node (via `AgentDeps.tracer`) and receives every model call through `LLMClient.on_call`; spans land in `trace_spans`. `KpiService` aggregates runs, reviewer decisions and traces into live KPIs and reads offline KPIs from `evals/results/`.
- Packaging (feat-018): `docker-compose.yml` runs `db` (pgvector), `seed` (one-shot `demo-seed` from `samples/demo/`), `api` (uvicorn, healthchecked) and `web` (nginx serving the built UI, proxying `/api`). `backend/Dockerfile` installs CPU-only PyTorch and bakes the embedding model in, then runs offline. CI also runs `eval-golden --check`, a database-free triage evaluation on the committed golden sample that fails on a drop of more than 0.02 macro-F1 below `evals/results/golden-baseline-astral-sh__uv.json`.
- MCP (`api/mcp_server.py`, CLI `mcp`): an `MCPServer` (MCP Python SDK 2.x) with `search_similar_issues` and `search_docs`, built on the same `SimilarIssueFinder` and `DocRetriever` as the agent; served over stdio, tested through an in-memory `mcp.Client`.
- Guardrails (`domain/guardrails.py`, `services/guardrails.py`): issue text is untrusted. Before any model call, secrets (tokens, keys, URL credentials, `*_PASSWORD=` values, private keys) and emails are replaced by `[REDACTED:<kind>]`, the body is fenced in `<issue>` delimiters (fake delimiters inside are neutralised), and injection phrases flag the issue for human review. After the call, every string field of the validated output is scanned again. On the 9,411 uv issues: 329 contain something redacted, 0 are flagged as injection after tuning out one false positive.
