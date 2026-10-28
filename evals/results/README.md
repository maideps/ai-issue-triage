# Evaluation results

Metric summaries only (no issue text, D-003). Each file records the prompt version, the model per step, the provider (`fake` or `real`), quality metrics, a majority-class reference, and token/cost/latency totals from the call ledger.

## Type triage (test split, 1,543 uv issues from 2025-09-12 on)

| Run | Provider | Accuracy | Macro-F1 | Cost | File |
|---|---|---|---|---|---|
| Majority class (`bug`) | — | 0.401 | 0.143 | $0 | — |
| Keyword heuristic, `triage@v1` | fake | 0.613 | 0.526 | $0 | [type-fake-triage-keywords-test.json](type-fake-triage-keywords-test.json) |
| Real model, `triage@v1` | real | pending: needs an API key (D-007) | | | |

Per class F1 for the keyword baseline: bug 0.68 (619 issues), enhancement 0.64 (457), question 0.47 (426), other 0.32 (41).

To run a real model, set the provider's key (for example `ANTHROPIC_API_KEY`) and map the step:

```bash
ISSUE_TRIAGE_LLM_MODELS='{"triage": "anthropic/claude-haiku-5-5"}' \
  node tools/py.mjs -m issue_triage eval-triage astral-sh/uv --limit 200
```

`--limit` keeps the first spend small; the summary file name includes the model.
