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

### D-008: Heading-based chunks with a local 384-dimension embedding model

**Date:** 2026-10-29
**Status:** accepted
**Context:** Doc-grounded answers (feat-011) need the target's documentation retrievable by issue text. Two chunkers were compared on a ground truth taken from maintainers: the uv docs pages linked in the first maintainer answer to a question (253 cases; 50 in the test split). Docs come from the last commit before the test split starts (21a92c1632, 72 pages), so test issues never see newer documentation (D-004). Embeddings: `sentence-transformers/all-MiniLM-L6-v2`, normalised, cosine distance in pgvector with an HNSW index.
**Decision:** Use heading-based chunks (one per section, breadcrumb prefix, long sections split by paragraph) as the default. On the 50 test cases: heading hit@1 0.240, hit@5 0.400, MRR 0.305 with 987 chunks; fixed 1,000-character windows with 200 overlap hit@1 0.200, hit@5 0.440, MRR 0.290 with 1,818 chunks. On all 253 cases (not time-valid for the train split, a larger-sample check only) fixed windows lead slightly (MRR 0.278 vs 0.255).
**Alternatives considered:**
- Fixed windows — Not chosen: no clear quality win at this sample size, nearly twice the chunks, and windows cut sections mid-sentence, which makes worse citations for drafted replies.
- A larger embedding model — Deferred: hybrid search and reranking (feat-009, feat-010) are the planned next levers; switching models changes `EMBEDDING_DIM` and needs a migration.
**Consequences:**
- Positive: self-contained, citable chunks; a smaller index.
- Negative: the vector column is fixed at 384 dimensions; the evaluation sample is small (50), so the comparison is indicative, not conclusive.

### D-009: Query rewriting on, cross-encoder reranking off for duplicate search

**Date:** 2026-10-30
**Status:** accepted
**Context:** feat-010 measured two standard retrieval upgrades on top of hybrid search (D-008, feat-009) against the 319 maintainer-linked duplicate pairs: LLM query rewriting (the issue becomes up to three focused queries, each searched with vector and BM25, all fused by RRF) and cross-encoder reranking of the top 20 (`cross-encoder/ms-marco-MiniLM-L-6-v2`, local). The rewriter ran on the deterministic fake (`fake/query-keyphrases`: title, first error line, inline code), per D-007.
**Decision:** Similar-issue search uses hybrid + query rewriting by default; reranking stays implemented but disabled. Results (recall@1 / @5 / @10, MRR@10, p50 latency): hybrid 0.144 / 0.351 / 0.467, 0.235, 601 ms; + rewrite 0.172 / 0.411 / 0.527, 0.278, 624 ms; + rerank 0.129 / 0.345 / 0.455, 0.223, 3,377 ms; + rewrite + rerank 0.141 / 0.367 / 0.480, 0.237, 5,810 ms.
**Alternatives considered:**
- Reranking on — Rejected: lower quality and five to ten times the latency. The MS MARCO cross-encoder scores web-query-to-passage relevance, not whether two bug reports describe the same problem, and truncates long issues.
- A larger or domain-tuned reranker — Deferred: worth testing once a real model or a fine-tuned pair classifier is available.
**Consequences:**
- Positive: +6 points of recall@10 for about 4% more latency, with no model spend; a documented negative result instead of an assumed improvement.
- Negative: rewriting adds one LLM call per issue once a real model replaces the fake.

### D-010: Native, LLM-free answer metrics until a judge model is configured

**Date:** 2026-10-30
**Status:** accepted (revisit when an API key is added)
**Context:** feat-011 planned RAGAS faithfulness and answer relevancy plus an LLM judge against the maintainer's real reply. RAGAS computes both metrics with an LLM (claim extraction, question generation), and no model key is configured (D-007); running RAGAS on the fake provider would produce meaningless numbers and pull in a large LangChain dependency tree.
**Decision:** Compute RAGAS-style metrics natively and deterministically: lexical faithfulness (share of reply sentences whose content words mostly appear in the cited chunks), answer relevancy as embedding cosine between issue and reply, reference similarity as embedding cosine between reply and maintainer answer, citation validity (citations must point at retrieved chunks, otherwise the draft goes to a human), and, for answers whose maintainer reply linked docs, whether the draft cited the same page. The LLM judge is a real pipeline step (`judge@v1` via `LLMClient`); on the fake provider it scores word overlap and is labelled as such. Swapping the `answer` and `judge` steps to real models through `ISSUE_TRIAGE_LLM_MODELS` makes the judge meaningful without code changes.
**Alternatives considered:**
- The `ragas` library now — Rejected: needs an LLM to be meaningful, adds LangChain, and its numbers would be fake-provider artefacts.
- No answer metrics until a key exists — Rejected: the grounding checks (citation validity, cited-page match) are objective today and catch the failure that matters most, unsupported answers.
**Consequences:**
- Positive: CI can run the answer evaluation on cached data with zero spend; the metrics are transparent and reproducible.
- Negative: lexical faithfulness is a proxy (paraphrases score low); the README must not present fake-judge scores as quality.

### D-011: One LangGraph flow, rule-based routing, a human gate on every proposal

**Date:** 2026-10-30
**Status:** accepted
**Context:** feat-012 assembles the pieces (guardrails, triage, similar-issue search, doc-grounded answers) into an agent. The ebook's five agent decisions apply: how many agents, what context each gets, where state lives, which decisions need no LLM, and when the system stops deciding.
**Decision:** One graph with single-purpose nodes rather than several autonomous agents: `guard → triage → retrieve → decide → draft_<action> → human_gate → finalize`. Each node declares the state keys it reads and writes, and a wrapper enforces both (`ContextViolation` on an undeclared write); hand-offs are Pydantic schemas stored as plain data. State lives in LangGraph's Postgres checkpointer on the application database, keyed by `owner/name#number`, so a failed run resumes from its last completed node and a pending review survives restarts. `decide` is code, not an LLM: duplicate when the best earlier issue's embedding similarity is at least 0.8, otherwise answer for questions, ask for information for bug reports with no version or reproduction, else a maintainer-facing summary. Every proposal stops at `human_gate` (`interrupt()`); `needs_attention` only orders the queue.
**Alternatives considered:**
- Separate agents per action that hand work to each other — Rejected: the actions share all upstream context and never run in parallel; splitting them adds hand-offs without a different decision to make.
- An LLM router for the action — Rejected: four outcomes from typed signals is a rule; an LLM would add cost and nondeterminism to the cheapest step.
- Auto-posting high-confidence drafts — Rejected: D-003 and uv's AI policy; a human approves everything.
**Consequences:**
- Positive: every node's context is inspectable in code; runs are resumable and auditable; the routing step costs nothing.
- Negative: the 0.8 duplicate threshold is not calibrated yet. In a real replay (#17846) the agent proposed #17791, a true duplicate the maintainers had not linked, while they linked #17711; the reviewer corrected it through an edit.

### D-012: A trained classifier triages first and escalates to the LLM below 0.5 confidence

**Date:** 2026-10-30
**Status:** accepted (re-tune when a real LLM replaces the keyword baseline)
**Context:** Type triage chooses among four known classes, the textbook case for a decision model instead of an LLM. The issue embeddings already stored for duplicate search (feat-009) make a classifier nearly free.
**Decision:** Logistic regression (balanced class weights) on the `all-MiniLM-L6-v2` issue embeddings, trained on the oldest 85% of the train split (4,351 issues). The escalation threshold is chosen on the newest 15% of the train split (768 issues) and only then reported on the 1,543-issue test split. Test macro-F1: classifier alone 0.580, LLM step alone (keyword fake) 0.526, classifier escalating below 0.5 to the LLM 0.592 with 34.2% of issues escalated. Prediction takes about 0.01 ms per issue once embedded. The agent's triage node uses this `EscalatingTriager` whenever `eval-decision` has saved a model (`data/models/`, trained on the full train split).
**Alternatives considered:**
- LLM for every issue — Rejected: lower macro-F1 here and one paid call per issue once a real model is configured.
- Picking the threshold on the test curve (0.4 scores 0.597) — Rejected: that would leak the test split into a design choice.
**Consequences:**
- Positive: two thirds of triage calls never reach an LLM; the decision is traceable (confidence and source per issue).
- Negative: the threshold was tuned against the keyword fake; a stronger real model will likely move it higher.

### D-013: Replay mode compares with maintainers; the decision rules need calibration

**Date:** 2026-10-30
**Status:** accepted (follow-up open: calibrate `decide` on the train split)
**Context:** feat-015 shows each suggestion beside what maintainers did: final type label, duplicate link, needs-info label and first maintainer reply. That information is from after `as_of`, so it is displayed and compared but never passed to the agent (D-004). `eval-replay` ran the whole agent on 100 test-split issues (25 each: duplicates, needs-info, answered questions, other types; seed 7).
**Decision:** Ship replay mode with per-aspect agreement (action, duplicate target, type) and publish the end-to-end baseline as is: action agreement 0.197 over 76 issues with a known maintainer action; by maintainer action, duplicate 0.276 (n=29, correct target 0.103), answer 0.261 (n=23), needs-info 0.042 (n=24); type agreement 0.651 (n=83). The agent chose summary for 67 of 100 issues.
**Alternatives considered:**
- Tuning the rules on these 100 test issues — Rejected: it would leak the test split.
- Hiding the end-to-end number behind the stronger per-component metrics — Rejected: the gap between components and the system is the most useful finding so far.
**Consequences:**
- Positive: the system-level weak spot is located precisely: `decide`'s needs-info rule (version or code present means "has info") is far stricter than maintainers, the 0.8 duplicate threshold is conservative, and summary absorbs everything else.
- Negative: until calibrated on the train split, end-to-end action agreement is low; per-component metrics overstate the system.

### D-014: Live mode only for allowlisted sandboxes; posting guarded in one place

**Date:** 2026-10-30
**Status:** accepted (live end-to-end check on a real sandbox not run yet)
**Context:** feat-016 lets new issues enter the queue as they are opened and lets approved replies be posted. D-003 forbids posting to real projects; uv's AI policy forbids AI communication with its maintainers.
**Decision:** Live mode is off unless `ISSUE_TRIAGE_SANDBOX_REPOSITORIES` lists repositories the author owns. Intake: a GitHub webhook (`POST /api/webhooks/github`, `issues` / `opened`) verified with `X-Hub-Signature-256` against `ISSUE_TRIAGE_WEBHOOK_SECRET`, or `sandbox-poll` for local use without a public URL; authors and mentions are pseudonymized like ingested issues. Posting: `SafePublisher` wraps the GitHub client so every call passes `check_allowed`, which refuses anything not on the allowlist and always refuses the evaluation target (`ISSUE_TRIAGE_REPOSITORY`, astral-sh/uv) even if listed. The review queue posts only the approved (possibly edited) text, only after approval, and records the comment URL.
**Alternatives considered:**
- A webhook-only design — Rejected: GitHub cannot reach localhost without a tunnel service; polling keeps the demo self-contained.
- Checking the allowlist in the API layer — Rejected: one guarded publisher means no code path can post around it.
**Consequences:**
- Positive: the only way to post is approval plus allowlist plus not-the-target, each covered by tests.
- Negative: the sandbox run against a real GitHub repository needs the author's go-ahead (it creates a repository and posts a comment) and has not been done; the automated tests use a recording poster.
