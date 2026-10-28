# Architectural Decisions Log

This file records significant architectural decisions, their context, alternatives considered, and consequences. It serves as durable cross-session memory for why the codebase is structured the way it is.

## Decision Template

```
### D-XXX: [Decision Title]

**Date:** YYYY-MM-DD
**Status:** proposed | accepted | deprecated | superseded by D-YYY
**Context:** [What problem are we solving? What constraints exist?]
**Decision:** [What did we decide to do?]
**Alternatives considered:**
- Alternative 1: [description] — Rejected because [reason]
**Consequences:**
- Positive: [benefits gained]
- Negative: [tradeoffs accepted]
```

## Recorded Decisions

<!-- Add decision records here, newest last. Expand long records into docs/decisions/ and link back from here. -->

### D-001: astral-sh/uv as the evaluation target

**Date:** 2026-10-27
**Status:** accepted
**Context:** The project needs a public issue tracker whose history contains real maintainer decisions to evaluate against: type labels, duplicate links, maintainer answers and requests for missing information. It must be active, labelled consistently and keep its docs in the same repository. `backend/scripts/scout_repos.py` scored 13 candidates on 2026-10-27; `backend/scripts/inspect_repo.py` then checked the 500 most recent closed uv issues (created 2026-02-14 to 2026-10-26) and all 487 duplicate closures.
**Decision:** Use astral-sh/uv. Measured ground truth: 80.4% of sampled issues carry a type label (bug 44.6%, question 20.4%, enhancement 13.2%) and only 0.6% carry two; 350 of 487 duplicates link to their original (226 through GitHub's `duplicateOf` field, 124 through comment text), 98.6% of them inside uv, and 90.5% of originals with dates were opened before the duplicate; 90.2% of `question` issues have a maintainer reply; 4.8% carry `needs-mre`. Area routing is out of scope: only 8.2% of sampled issues carry an `area:` label.
**Alternatives considered:**
- vitejs/vite (most duplicates, 340) — Rejected because questions mostly go to Discussions, leaving few answerable-from-docs issues.
- pola-rs/polars — Kept as a fallback; fewer duplicates (104) for the duplicate-detection eval.
- fastapi/fastapi — Rejected: support moved to Discussions (86 issues in the last year).
- supabase/supabase — Rejected: only 26 duplicates.
- microsoft/playwright — Rejected: 28% of issues labelled.
- streamlit/streamlit, langchain-ai/langgraph — Rejected: docs live in a separate repository.
**Consequences:**
- Positive: four evaluable tasks with real ground truth; a Python tool whose issues the author understands; plenty of error output to exercise hybrid search.
- Negative: classes are imbalanced (report macro-F1); 137 duplicates have no link the scripts could find yet; about 10% of linked originals are newer than the duplicate and must be excluded from time-split evaluation.

### D-002: Tech stack and scope for the portfolio project

**Date:** 2026-10-27
**Status:** accepted
**Context:** The goal is a public portfolio piece showing the AI engineering skills that ai-invoice-intake does not already show: retrieval, agent orchestration, evaluation against ground truth, and cost control. No employer code or data may be used.
**Decision:** FastAPI backend in Python; PostgreSQL with pgvector as both the relational store and the vector index; LiteLLM behind a provider interface with a deterministic fake for tests and the default demo; LangGraph with a Postgres checkpointer for the agent flow; RAGAS plus task metrics for evaluation; a React + TypeScript review UI; Docker Compose and GitHub Actions running the harness check and a cached evaluation suite.
**Alternatives considered:**
- A dedicated vector database (Qdrant, Pinecone) — Rejected: pgvector keeps one engine for relational data and vectors, and the corpus is small.
- Streamlit for the UI — Rejected for the final UI in favour of React for hiring recognition; it may still be used for internal evaluation views.
- Calling one provider SDK directly — Rejected: switching models per step must be a configuration change.
**Consequences:**
- Positive: one coherent stack that covers all five layers of an AI product; runs without an API key.
- Negative: a large stack to keep green in CI; the real-model evaluation costs money, so its results are cached and committed as summaries.

### D-003: Never post to real repositories

**Date:** 2026-10-27
**Status:** accepted
**Context:** uv has a written AI policy: AI should not be used to communicate with maintainers, and bot labels (`bot:ai-policy-comment`, `bot:ai-policy-close`) enforce it. Installing a bot on any repository also requires its maintainers' consent. GitHub data is used under its terms of service.
**Decision:** The system only replays historical issues and posts drafts to a sandbox repository owned by the author, enforced by an allowlist in configuration and covered by a test. Raw GitHub data stays local and git-ignored; usernames are pseudonymized at ingestion; only a small sample and the fetch scripts are committed.
**Alternatives considered:**
- Commenting on real uv issues — Rejected: violates the project's AI policy and would harm the author's reputation.
- Committing a full data dump — Rejected: unnecessary redistribution of user content.
**Consequences:**
- Positive: an honest, defensible scope that doubles as an interview topic.
- Negative: no real end users; value is demonstrated through replay against ground truth and the sandbox demo.

### D-004: Time-split evaluation

**Date:** 2026-10-27
**Status:** accepted
**Context:** If a replayed issue can retrieve issues, comments or docs created after it, the system "finds" answers from the future and every metric is inflated.
**Decision:** Every evaluation case carries an as-of timestamp (the issue's creation time). Retrieval filters the index to items created before it, docs are taken from the repository state at that date when available, and the trained classifier is trained on older issues and tested on newer ones. Duplicate pairs whose original is newer than the duplicate are excluded.
**Alternatives considered:**
- Random train/test split — Rejected: leaks future information through near-identical issues.
**Consequences:**
- Positive: metrics that hold up under scrutiny.
- Negative: more complex retrieval filters and dataset builders; slightly lower headline numbers.

### D-005: Known baseline failure of `./init.sh` from a framework test

**Date:** 2026-10-27
**Status:** resolved 2026-10-27 (fixed upstream in ai-harness-framework 1f526d5, applied via harness-upgrade)
**Context:** `./init.sh` runs the verification chain with `CW_INIT_SH=1` so the placeholder check only warns on a first run. The runner unit test "ensureNoPlaceholders: a fresh create-harness output fails lint and arch-003 (#23)" in `tests/framework-check.test.mjs` spawns the runner with `{ ...process.env }`, inherits `CW_INIT_SH=1`, sees lint warn instead of fail, and fails. It passes when run alone and under `npm run check`. Reproduced 3/3 here and in ai-harness-framework itself at commit b77ec83.
**Decision:** Do not edit CORE files in this repo. Fix it upstream (the test's `run()` helper should drop `CW_INIT_SH` unless the test sets it), then apply it here with `harness-upgrade`. Until then, `npm run check` is the baseline verification and a failing `./init.sh` whose only failure is this test is not a blocker.
**Alternatives considered:**
- Patch `tests/framework-check.test.mjs` here — Rejected because CORE files must not be edited in adopter repos and the change would be lost on upgrade.
**Consequences:**
- Positive: the harness contract stays intact and the bug gets fixed at its source.
- Negative: `./init.sh` reports FAIL until the upstream fix lands.

### D-006: Backend tooling runs through a small Node launcher

**Date:** 2026-10-27
**Status:** accepted
**Context:** The harness resolves product verification layers from the root `package.json` scripts, but the backend is Python. The author works on Windows (virtualenv paths are `.venv/Scripts`, not `.venv/bin`) and does not have `uv` installed. ai-invoice-intake solved the same problem with a launcher (its D-003).
**Decision:** `tools/py.mjs` finds the interpreter (`$ISSUE_TRIAGE_PYTHON`, then `backend/.venv`, then the system Python), prints a hint when dependencies are missing, and offers `setup`, which creates `backend/.venv` and installs the backend with dev tools. The root `lint`, `typecheck`, `test` and `build` scripts keep the harness's own check and append the backend command with `&&`. Ruff also covers `backend/scripts/`; mypy covers `src/` and `tests/`. CI installs Python 3.12 and runs `npm run setup:backend` before the check.
**Alternatives considered:**
- `uv` for environments and commands — Rejected for now: not installed on the author's machine, and the launcher already works across operating systems. Worth revisiting if dependency resolution gets slow.
- A root `pyproject.toml` — Rejected: stack detection would pick Python and skip the harness's Node checks.
**Consequences:**
- Positive: one command surface (`npm run check`) covers harness and backend on every OS, matching the author's other repos.
- Negative: a fresh clone must run `npm run setup:backend` once; the launcher says so when it is missing. Dev dependency `httpx2` replaces `httpx` because the Starlette test client deprecates `httpx` (switched in session 004, as in ai-invoice-intake).

### D-007: Build on a fake LLM and local models; run gates without the framework's adoption matrix

**Date:** 2026-10-27
**Status:** accepted
**Context:** The author asked for all remaining features to be built in one autonomous run, committing and pushing after each. No LLM API key is configured on the machine. The full `npm run check` takes about 8.5 minutes locally, almost all of it in the e2e layer's adoption matrix, which generates throwaway repos to test the harness framework itself, not this product.
**Decision:** (1) Every LLM-backed step runs on the deterministic fake provider; real-model evaluation runs are left for when the author adds a key, and the eval tooling caches and commits only summaries. (2) Embeddings and the reranker use local sentence-transformers models. (3) The repository stays private until the README carries real numbers. (4) Feature gates run locally with `CW_SKIP_ADOPTION_MATRIX=1`, the same setting CI already uses; the matrix still runs on any manual `npm run check` without the variable. (5) New dependencies are limited to the D-002 stack the author approved.
**Alternatives considered:**
- Real-model runs during the build — Rejected for now: no key, and spend needs the author's approval.
- API embeddings — Rejected: needs a key and costs money per index; local models are reproducible.
- Running the adoption matrix in every gate — Rejected: about 7 extra minutes per feature for a test of the framework, not the product.
**Consequences:**
- Positive: the whole product builds and runs without secrets or spend; gates take minutes, not hours.
- Negative: headline LLM metrics stay "fake provider" until a real run; PyTorch and model weights make the backend install heavy.
