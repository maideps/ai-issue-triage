# Evaluation results

Metric summaries only (no issue text, D-003). Each file records the prompt version, the model per step, the provider (`fake` or `real`), quality metrics, a majority-class reference, and token/cost/latency totals from the call ledger.

## Type triage (test split, 1,543 uv issues from 2025-09-12 on)

| Run | Provider | Accuracy | Macro-F1 | Cost | File |
|---|---|---|---|---|---|
| Majority class (`bug`) | — | 0.401 | 0.143 | $0 | — |
| Keyword heuristic, `triage@v1` | fake | 0.613 | 0.526 | $0 | [type-fake-triage-keywords-test.json](type-fake-triage-keywords-test.json) |
| Real model, `triage@v1` | real | pending: needs an API key (D-007) | | | |

Per class F1 for the keyword baseline: bug 0.68 (619 issues), enhancement 0.64 (457), question 0.47 (426), other 0.32 (41).

## Duplicate detection (feat-009)

Ground truth: the original linked when maintainers closed an issue as a duplicate. Every search is restricted to issues created before the duplicate, so all 319 pairs are time-valid (the train/test split only matters for trained models).

| Mode | recall@1 | recall@5 | recall@10 | MRR@10 | Test split (n=53) recall@10 |
|---|---|---|---|---|---|
| Vector (`all-MiniLM-L6-v2`, pgvector HNSW) | 0.141 | 0.301 | 0.398 | 0.211 | 0.453 |
| Keyword (BM25) | 0.154 | 0.314 | 0.401 | 0.219 | 0.472 |
| Hybrid (reciprocal rank fusion) | 0.144 | 0.351 | 0.467 | 0.235 | 0.509 |

Files: [duplicates-all.json](duplicates-all.json), [duplicates-test.json](duplicates-test.json). Keyword search alone matches embeddings because duplicate reports share error strings and version specifiers; fusing both adds about 7 points of recall@10.

## Query rewriting and reranking (feat-010, D-009)

Same 319 pairs, on top of hybrid search. Rewriting uses the fake `query-keyphrases` step (title, first error line, inline code); reranking uses `cross-encoder/ms-marco-MiniLM-L-6-v2` on the top 20.

| Variant | recall@1 | recall@5 | recall@10 | MRR@10 | p50 latency |
|---|---|---|---|---|---|
| Hybrid | 0.144 | 0.351 | 0.467 | 0.235 | 601 ms |
| + query rewrite | 0.172 | 0.411 | 0.527 | 0.278 | 624 ms |
| + cross-encoder rerank | 0.129 | 0.345 | 0.455 | 0.223 | 3,377 ms |
| + rewrite + rerank | 0.141 | 0.367 | 0.480 | 0.237 | 5,810 ms |

File: [rerank-all.json](rerank-all.json). The general-purpose cross-encoder hurts: it was trained on web queries, not issue-to-issue similarity. Rewriting is on by default, reranking off.

## Doc retrieval: chunking strategies (D-008)

Ground truth: the uv docs pages linked in the first maintainer answer to a question. Docs at commit 21a92c1632 (last before the test split), `all-MiniLM-L6-v2` embeddings, pgvector HNSW.

| Chunker | Chunks | Test (n=50) hit@1 | hit@5 | MRR | All (n=253) MRR |
|---|---|---|---|---|---|
| Heading sections | 987 | 0.240 | 0.400 | 0.305 | 0.255 |
| Fixed 1,000 chars, 200 overlap | 1,818 | 0.200 | 0.440 | 0.290 | 0.278 |

Files: [chunking-test.json](chunking-test.json), [chunking-all.json](chunking-all.json). The "all" run is not time-valid for train-split issues (they predate the docs snapshot); it is a larger-sample check only.

To run a real model, set the provider's key (for example `ANTHROPIC_API_KEY`) and map the step:

```bash
ISSUE_TRIAGE_LLM_MODELS='{"triage": "anthropic/claude-haiku-5-5"}' \
  node tools/py.mjs -m issue_triage eval-triage astral-sh/uv --limit 200
```

`--limit` keeps the first spend small; the summary file name includes the model.
