# Learning Log

One entry per milestone: what I built, what I measured, what surprised me and what I would do differently. The entries double as drafts for public posts.

| Milestone | Entry | Headline number |
|---|---|---|
| M0 Foundations | to write | 9,411 uv issues; 6,662 typed, 319 duplicate pairs, 1,997 answered questions ([stats](../../evals/datasets/astral-sh__uv-stats.json)) |
| M1 Production-grade LLM calls | to write | Keyword triage macro-F1 0.526 vs 0.143 majority ([summary](../../evals/results/type-fake-triage-keywords-test.json)) |
| M2 Data and RAG | to write | Hybrid duplicate recall@10 0.467 vs 0.40 single-method ([summary](../../evals/results/duplicates-all.json)) |
| M3 Advanced RAG | to write | Query rewriting 0.527 recall@10; reranking lowered it to 0.455 ([summary](../../evals/results/rerank-all.json)) |
| M4 Agents | to write | Resumable LangGraph runs with a human gate; replay of #17846 found a true duplicate |
| M5 Decision models | to write | Classifier + escalation macro-F1 0.592 with 34% of LLM calls ([summary](../../evals/results/decision-model-test.json)) |
| M6 Product and production | to write | End-to-end action agreement 20% ([summary](../../evals/results/replay-test.json)); one-command Docker demo |
| M7 Integration | to write | MCP search tools; README with every number linked |

## Entry template

```
# M<n>: <title>

**Date:** YYYY-MM-DD · **Features:** feat-0xx, feat-0yy

## What I built
## What I measured (with links to evals/results/)
## What surprised me
## What I would do differently
## Concepts I can now explain in an interview
```
