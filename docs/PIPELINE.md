# Pipeline, step by step

Every stage of the project as a command, in the order it was built. The one-command demo in the [README](../README.md#run-it-in-five-minutes) runs a small committed sample; these commands rebuild everything from the full astral-sh/uv history.

## Backend quick start

Requires Node.js >= 18 (the harness) and Python >= 3.11.

```bash
npm run setup:backend   # creates backend/.venv and installs the backend with dev tools
npm run dev:backend     # serves http://localhost:8000 (docs at /docs)
curl http://localhost:8000/health
```

Configuration comes from `ISSUE_TRIAGE_*` environment variables (see [`backend/.env.example`](../backend/.env.example)). The harness commands cover the backend: `npm run lint` (ruff), `npm run typecheck` (mypy --strict), `npm run test` (pytest) and `npm run build` (compile check), all through [`tools/py.mjs`](../tools/py.mjs) ([D-006](../DECISIONS.md#d-006-backend-tooling-runs-through-a-small-node-launcher)).

## Ingest issues

```bash
node tools/py.mjs -m issue_triage ingest astral-sh/uv            # resumable; run again to continue
node tools/py.mjs -m issue_triage ingest astral-sh/uv --restart  # start over
```

Issues, labels, state reasons, `duplicateOf` links and up to 50 comments per issue are fetched oldest first through the GitHub GraphQL API (token from `ISSUE_TRIAGE_GITHUB_TOKEN` or `gh auth token`). Usernames and @mentions outside code are replaced by keyed pseudonyms before anything is written, and the data stays in the git-ignored `data/` folder ([D-003](../DECISIONS.md#d-003-never-post-to-real-repositories)).

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

Four time-split sets from 9,411 uv issues: 6,662 typed issues, 319 duplicate pairs, 1,997 question/answer pairs and 6,912 needs-info cases (387 positive). Details and rules in [`evals/datasets/`](../evals/datasets/README.md).

## Index the documentation

```bash
node tools/py.mjs -m issue_triage docs-index astral-sh/uv   # docs as of the test-split start, both chunkers
node tools/py.mjs -m issue_triage eval-docs astral-sh/uv    # compare chunkers on maintainer-linked pages
```

72 uv docs pages become 987 heading chunks (default, [D-008](../DECISIONS.md#d-008-heading-based-chunks-with-a-local-384-dimension-embedding-model)) or 1,818 fixed windows, embedded locally with `all-MiniLM-L6-v2` into pgvector with an HNSW index. Test-split MRR: 0.305 (heading) vs 0.290 (fixed).

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
| Hybrid + query rewriting (default, [D-009](../DECISIONS.md#d-009-query-rewriting-on-cross-encoder-reranking-off-for-duplicate-search)) | 0.527 | 0.278 |
| Hybrid + cross-encoder rerank | 0.455 | 0.223 |

## Draft doc-grounded answers

```bash
node tools/py.mjs -m issue_triage eval-answers astral-sh/uv     # 370 test-split questions
```

Drafts cite the retrieved doc chunks they rely on; a citation to anything not retrieved, low confidence or no citation sends the draft to a human. Baseline with the fake drafter: citation validity 1.00, lexical faithfulness 0.71, reply-to-maintainer similarity 0.39, same docs page as the maintainer in 24% of linked cases ([D-010](../DECISIONS.md#d-010-native-llm-free-answer-metrics-until-a-judge-model-is-configured), [results](../evals/results/README.md)).

## Run the agent

```bash
node tools/py.mjs -m issue_triage agent-run astral-sh/uv 17846                 # replay as of its creation
node tools/py.mjs -m issue_triage agent-review astral-sh/uv 17846 --approve    # or --edit "..." / --reject
```

A LangGraph flow (guardrails → triage → similar issues → rule-based decision → draft → human gate) with a Postgres checkpointer: the run waits at the gate across restarts and resumes from its last completed node after a failure ([D-011](../DECISIONS.md#d-011-one-langgraph-flow-rule-based-routing-a-human-gate-on-every-proposal)). Replaying #17846, it proposed a duplicate of #17791, the same macOS torch-reinstall bug; maintainers had linked #17711, so the review edited the reply.

## Review suggestions in the UI

```bash
npm run setup:frontend                                   # once
npm run dev:backend                                      # FastAPI on :8000 (loads models on first request)
npm run dev:frontend                                     # React + Vite on :4173, proxies /api
curl -X POST http://localhost:8000/api/runs/astral-sh/uv/16021   # replay an issue into the queue
```

The queue lists pending suggestions (attention first); a suggestion opens beside the original issue with its evidence: the rule that chose the action, the triage label, cited doc chunks and similar earlier issues. Approve, edit or reject resumes the agent and stores the reviewer's decision as feedback.

## Replay mode

In the UI, enter any uv issue number to replay it as of its creation date. The suggestion appears beside what maintainers actually did (type, duplicate link, request for a reproduction, first reply) with per-aspect agreement. Across 100 replayed test issues the agent matched the maintainers' action 20% of the time while matching their type label 65% of the time: the rule-based decision step is the bottleneck, not the components ([D-013](../DECISIONS.md#d-013-replay-mode-compares-with-maintainers-the-decision-rules-need-calibration)).

```bash
node tools/py.mjs -m issue_triage eval-replay astral-sh/uv
```

## Calibrate the routing

```bash
node tools/py.mjs -m issue_triage calibrate-routing astral-sh/uv            # policy + needs-info model into data/models/
node tools/py.mjs -m issue_triage eval-replay astral-sh/uv --routing legacy  # compare with the hand-written rules
node tools/py.mjs -m issue_triage eval-replay astral-sh/uv                   # calibrated (default when the files exist)
```

Models are fit on the oldest 85% of the train split, the policy is chosen on the newest 15%, and the test replay is only run afterwards ([D-015](../DECISIONS.md#d-015-calibrated-routing-a-needs-info-classifier-and-tuned-thresholds)).

## Use the search tools from any agent (MCP)

```bash
claude mcp add ai-issue-triage -- node tools/py.mjs -m issue_triage mcp   # from the repository root
```

The MCP server exposes two read-only tools over stdio: `search_similar_issues` (hybrid search over earlier issues, with the same `before` as-of filter the agent uses) and `search_docs` (citable doc excerpts with page URLs). Asked "as of" 2026-02-04 about the macOS torch-reinstall bug (#17846), it returns #17791 and #17711, the issue maintainers linked, as the top two.

## Traces and KPIs

Every agent run is traced per node and per model call (tokens, cost, latency, status), and `GET /api/kpis` combines live numbers (approval and edit rate, escalation to the LLM, cost per issue, latency p50/p95, errors) with the offline evaluation results; the UI shows them above the queue. Details: [docs/OBSERVABILITY.md](OBSERVABILITY.md#product-observability-feat-017). The first run after a restart pays the model loading (about 13 s in the triage node); later runs take about 1-2 s on CPU.

## Live mode on your own sandbox

Off by default. To try it on a repository you own (never on someone else's project, [D-014](../DECISIONS.md#d-014-live-mode-only-for-allowlisted-sandboxes-posting-guarded-in-one-place)):

```bash
export ISSUE_TRIAGE_SANDBOX_REPOSITORIES='["you/your-sandbox"]'
node tools/py.mjs -m issue_triage sandbox-poll you/your-sandbox   # queue its open issues
npm run dev:backend && npm run dev:frontend                       # review and approve in the UI
```

Approving posts the approved text as a comment on that sandbox issue; rejecting posts nothing. With a public URL, point a GitHub webhook (`issues` events, secret in `ISSUE_TRIAGE_WEBHOOK_SECRET`) at `/api/webhooks/github` instead of polling. Posting to astral-sh/uv is refused in code even if it is listed.

## Evaluate triage

```bash
node tools/py.mjs -m issue_triage eval-triage astral-sh/uv            # test split, fake provider
```

| Type triage, 1,543 test issues | Accuracy | Macro-F1 | Cost |
|---|---|---|---|
| Majority class | 0.401 | 0.143 | $0 |
| Keyword baseline (fake provider) | 0.613 | 0.526 | $0 |
| Real model | pending an API key | | |
| Trained classifier, escalating below 0.5 (default, [D-012](../DECISIONS.md#d-012-a-trained-classifier-triages-first-and-escalates-to-the-llm-below-05-confidence)) | — | 0.592 | 34% of LLM calls |

Results and how to run a real model: [`evals/results/`](../evals/results/README.md).

## Target analysis

The scripts that chose the target live in [`backend/scripts/`](../backend/scripts/) and use the authenticated `gh` CLI:

```bash
python backend/scripts/scout_repos.py                 # score candidate repos
python backend/scripts/inspect_repo.py astral-sh/uv   # label quality and duplicate links
```

