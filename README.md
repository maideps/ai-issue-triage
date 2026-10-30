# AI Issue Triage

**A new issue arrives; the AI labels it, looks for duplicates, drafts a reply grounded in the project's docs, and a maintainer decides.** An issue-triage copilot built on retrieval (hybrid search, reranking, pgvector) and an agent graph (LangGraph) with a human gate, evaluated against what real maintainers actually did.

> **Portfolio project.** It learns from and is evaluated on the public issue history of [astral-sh/uv](https://github.com/astral-sh/uv). It **never posts to real repositories**: it replays historical issues and demos live on a sandbox repo only ([D-003](DECISIONS.md#d-003-never-post-to-real-repositories)). Built with [ai-harness-framework](https://github.com/maideps/ai-harness-framework): one feature at a time, with layered verification and durable session state in this repository.

**Stack (planned):** FastAPI · PostgreSQL + pgvector · LiteLLM (provider abstraction, deterministic fake by default) · LangGraph · RAGAS · React + TypeScript · Docker Compose · GitHub Actions

## Status

Early development. The FastAPI service skeleton is in place (`GET /health`, typed settings, layered packages with their import rules tested). See [`feature_list.json`](feature_list.json) for the plan and [`PROGRESS.md`](PROGRESS.md) for the current state.

## Backend quick start

Requires Node.js >= 18 (the harness) and Python >= 3.11.

```bash
npm run setup:backend   # creates backend/.venv and installs the backend with dev tools
npm run dev:backend     # serves http://localhost:8000 (docs at /docs)
curl http://localhost:8000/health
```

Configuration comes from `ISSUE_TRIAGE_*` environment variables (see [`backend/.env.example`](backend/.env.example)). The harness commands cover the backend: `npm run lint` (ruff), `npm run typecheck` (mypy --strict), `npm run test` (pytest) and `npm run build` (compile check), all through [`tools/py.mjs`](tools/py.mjs) ([D-006](DECISIONS.md#d-006-backend-tooling-runs-through-a-small-node-launcher)).

## What it does

| Task | Ground truth it is measured against | Metric |
|---|---|---|
| **Triage by type** (bug / question / enhancement / other) | Labels maintainers applied (80% of uv issues carry one) | Macro-F1 |
| **Duplicate detection** | Issues closed as duplicates with a link to the original (~300 time-valid pairs) | Recall@5, MRR |
| **Doc-grounded answers** to questions | The maintainer reply that resolved the issue (90% of uv questions have one) | Faithfulness (RAGAS), LLM judge vs. real reply |
| **Missing-info detection** (needs a reproducible example) | `needs-mre` label | Precision / recall |

Every evaluation is **time-split**: an issue is replayed with an index that only holds issues and docs created before it ([D-004](DECISIONS.md#d-004-time-split-evaluation)). Routing by area is out of scope: only 8% of recent uv issues carry an `area:` label ([D-001](DECISIONS.md#d-001-astral-shuv-as-the-evaluation-target)).

## Roadmap

Each milestone ends with numbers in this README and an entry in the [learning log](docs/learning-log/README.md).

| Milestone | Features | What it proves |
|---|---|---|
| M0 Foundations | feat-001 to feat-004 | Service skeleton, issue ingestion, persistence, ground-truth datasets |
| M1 Production-grade LLM calls | feat-005 to feat-007 | Provider abstraction, guardrails, LLM triage baseline with an eval runner |
| M2 Data and RAG | feat-008, feat-009 | Docs chunking in pgvector, hybrid duplicate search |
| M3 Advanced RAG | feat-010, feat-011 | Reranking, query transformation, doc-grounded answers with RAGAS |
| M4 Agents | feat-012 | LangGraph flow with checkpoints and a human gate |
| M5 Decision models | feat-013 | A trained classifier vs. the LLM on cost, latency and F1 |
| M6 Product and production | feat-014 to feat-018 | Review UI, replay and sandbox modes, observability, Docker and CI with evals |
| M7 Integration | feat-019, feat-020 | MCP server, final README, diagram and demo |

## Ingest issues

```bash
node tools/py.mjs -m issue_triage ingest astral-sh/uv            # resumable; run again to continue
node tools/py.mjs -m issue_triage ingest astral-sh/uv --restart  # start over
```

Issues, labels, state reasons, `duplicateOf` links and up to 50 comments per issue are fetched oldest first through the GitHub GraphQL API (token from `ISSUE_TRIAGE_GITHUB_TOKEN` or `gh auth token`). Usernames and @mentions outside code are replaced by keyed pseudonyms before anything is written, and the data stays in the git-ignored `data/` folder ([D-003](DECISIONS.md#d-003-never-post-to-real-repositories)).

## Load issues into PostgreSQL

```bash
docker compose up -d db                                  # PostgreSQL 16 + pgvector on port 5433
node tools/py.mjs -m issue_triage db-upgrade             # apply Alembic migrations
node tools/py.mjs -m issue_triage load astral-sh/uv      # idempotent upsert from data/
```

Database tests use the same container (database `issue_triage_test`, recreated per run). They skip when Postgres is unreachable unless `ISSUE_TRIAGE_REQUIRE_DB=1`, which CI and the feature gates set.

## Build the evaluation datasets

```bash
node tools/py.mjs -m issue_triage datasets astral-sh/uv --golden 50
```

Four time-split sets from 9,411 uv issues: 6,662 typed issues, 319 duplicate pairs, 1,997 question/answer pairs and 6,912 needs-info cases (387 positive). Details and rules in [`evals/datasets/`](evals/datasets/README.md).

## Index the documentation

```bash
node tools/py.mjs -m issue_triage docs-index astral-sh/uv   # docs as of the test-split start, both chunkers
node tools/py.mjs -m issue_triage eval-docs astral-sh/uv    # compare chunkers on maintainer-linked pages
```

72 uv docs pages become 987 heading chunks (default, [D-008](DECISIONS.md#d-008-heading-based-chunks-with-a-local-384-dimension-embedding-model)) or 1,818 fixed windows, embedded locally with `all-MiniLM-L6-v2` into pgvector with an HNSW index. Test-split MRR: 0.305 (heading) vs 0.290 (fixed).

## Find duplicates

```bash
node tools/py.mjs -m issue_triage issues-embed astral-sh/uv      # 9,411 issue embeddings into pgvector
node tools/py.mjs -m issue_triage eval-duplicates astral-sh/uv --split all
```

| Duplicate search, 319 maintainer-linked pairs | recall@10 | MRR@10 |
|---|---|---|
| Vector | 0.398 | 0.211 |
| Keyword (BM25) | 0.401 | 0.219 |
| Hybrid (RRF) | 0.467 | 0.235 |
| Hybrid + query rewriting (default, [D-009](DECISIONS.md#d-009-query-rewriting-on-cross-encoder-reranking-off-for-duplicate-search)) | 0.527 | 0.278 |
| Hybrid + cross-encoder rerank | 0.455 | 0.223 |

## Draft doc-grounded answers

```bash
node tools/py.mjs -m issue_triage eval-answers astral-sh/uv     # 370 test-split questions
```

Drafts cite the retrieved doc chunks they rely on; a citation to anything not retrieved, low confidence or no citation sends the draft to a human. Baseline with the fake drafter: citation validity 1.00, lexical faithfulness 0.71, reply-to-maintainer similarity 0.39, same docs page as the maintainer in 24% of linked cases ([D-010](DECISIONS.md#d-010-native-llm-free-answer-metrics-until-a-judge-model-is-configured), [results](evals/results/README.md)).

## Run the agent

```bash
node tools/py.mjs -m issue_triage agent-run astral-sh/uv 17846                 # replay as of its creation
node tools/py.mjs -m issue_triage agent-review astral-sh/uv 17846 --approve    # or --edit "..." / --reject
```

A LangGraph flow (guardrails → triage → similar issues → rule-based decision → draft → human gate) with a Postgres checkpointer: the run waits at the gate across restarts and resumes from its last completed node after a failure ([D-011](DECISIONS.md#d-011-one-langgraph-flow-rule-based-routing-a-human-gate-on-every-proposal)). Replaying #17846, it proposed a duplicate of #17791, the same macOS torch-reinstall bug; maintainers had linked #17711, so the review edited the reply.

## Evaluate triage

```bash
node tools/py.mjs -m issue_triage eval-triage astral-sh/uv            # test split, fake provider
```

| Type triage, 1,543 test issues | Accuracy | Macro-F1 | Cost |
|---|---|---|---|
| Majority class | 0.401 | 0.143 | $0 |
| Keyword baseline (fake provider) | 0.613 | 0.526 | $0 |
| Real model | pending an API key | | |
| Trained classifier, escalating below 0.5 (default, [D-012](DECISIONS.md#d-012-a-trained-classifier-triages-first-and-escalates-to-the-llm-below-05-confidence)) | — | 0.592 | 34% of LLM calls |

Results and how to run a real model: [`evals/results/`](evals/results/README.md).

## Target analysis

The scripts that chose the target live in [`backend/scripts/`](backend/scripts/) and use the authenticated `gh` CLI:

```bash
python backend/scripts/scout_repos.py                 # score candidate repos
python backend/scripts/inspect_repo.py astral-sh/uv   # label quality and duplicate links
```

## Quick Start

```bash
# 1. Verify the environment is healthy
./init.sh

# 2. Run full verification (make and npm forms are 1:1 mirrors)
make check          # or: npm run check

# 3. See all available commands
make help           # or: npm run help
```

### Prerequisites

- **Node.js** ≥ 18 — the harness runtime (stack detection, verification layers, session traces)
- **Make** — optional; `npm run <target>` mirrors every make target
- **Python** ≥ 3.11 and the **GitHub CLI** (`gh auth login`) — for the backend and the target-analysis scripts

## Verification Layers

| Layer | Command | What It Checks |
|-------|---------|----------------|
| 1 | `make lint` / `npm run lint` | Static analysis |
| 1b | `make typecheck` / `npm run typecheck` | Type safety |
| 2 | `make test` / `npm run test` | Unit and integration tests |
| 3 | `make build` / `npm run build` | Build verification |
| 3b | `make e2e` / `npm run e2e` | End-to-end tests |

Layers that are not configured report **SKIP** and do not count as verified.

### Harness Commands

The complete command list is generated from the Makefile and npm scripts. See [Harness Command Reference](docs/HARNESS.md); regenerate it with `npm run harness-docs`.

## State Files

- `feature_list.json` — feature declarations: id, behavior, dependencies, verification layers
- `.harness/state/<id>.json` — that feature's mutable state (`state`, `evidence`); one file per feature
- `PROGRESS.md` — generated current-state dashboard and session index
- `progress/NNN-YYYY-MM-DD.md` — one session's notes
- `DECISIONS.md` — architectural decisions log
- `session-handoff.md` — multi-session handoff notes

## Documentation

- [Product Overview](docs/PRODUCT.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Learning Log](docs/learning-log/README.md)
- [Observability](docs/OBSERVABILITY.md)
- [Tools & MCP](docs/TOOLS.md)
- [Usage Guide](docs/USAGE.md)
- [Quality Document](docs/quality-document.md)

## License

MIT — see [LICENSE](LICENSE).
