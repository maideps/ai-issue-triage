# Evaluation datasets

Built by `node tools/py.mjs -m issue_triage datasets astral-sh/uv --golden 50` from the issues loaded into PostgreSQL.

| Set | Input | Ground truth | Cases (uv) | Test split |
|---|---|---|---|---|
| `type` | title + body | the single type class from maintainer labels (bug / question / enhancement / other; conflicting labels excluded) | 6,662 | 1,543 |
| `duplicates` | title + body | the original the issue was closed as a duplicate of (`duplicateOf` field or a "Duplicate of #N" comment), only when the original is older | 319 | 53 |
| `answers` | title + body | the first maintainer reply to a `question` issue (not by its author) | 1,997 | 370 |
| `needs_info` | title + body | whether the issue carries `needs-mre` (positives) vs. any other typed issue | 6,912 (387 positive) | 1,582 |

Exact counts: [`astral-sh__uv-stats.json`](astral-sh__uv-stats.json).

## Rules (D-004)

- Every case has `as_of`, the issue's creation time. The model sees only the title and body.
- The test split is the newest 20% of issues by creation time (from 2025-09-12 for uv); anything trained on data must use only the train split.
- Duplicate pairs whose original is newer than the duplicate are dropped: maintainers sometimes merge into a fresh tracking issue, which nobody could have retrieved at `as_of`.

## What is committed

The full case files contain issue text, so they stay in the git-ignored `data/datasets/` folder (D-003). This folder holds only the statistics and [`golden/astral-sh__uv-golden.jsonl`](golden/astral-sh__uv-golden.jsonl): 50 test-split issues sampled per task (10 bug, 10 question, 10 enhancement, 5 other, 10 duplicates, 5 needs-info), pseudonymized, bodies cut at 1,500 characters.

The golden sample's labels are the maintainers'. Claude checked every title against its labels for consistency on 2026-10-27 and found no mismatches; `reviewed_by` stays empty until the author reviews it.
