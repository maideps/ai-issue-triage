# Observability

## Sprint Contract

Before starting each feature, fill out a sprint contract (`templates/sprint-contract.md`) that declares:

- What you intend to build
- How you'll verify it's correct
- What success looks like
- How long you expect it to take

## Session Traces

```bash
make session-start    # or: npm run session-start
make session-end      # or: npm run session-end
```

Traces are stored in `.harness/traces/` as timestamped JSON records with:

- Session start/end times
- Active feature ID
- Verification metadata (active feature layers and recorded evidence)
- Files modified
- Decision summary (count and latest decision title)

## Evaluator Rubric

After completing a sprint, score it against `templates/evaluator-rubric.md`. Every dimension must reach B or above before the feature is considered complete.

## Quality Document

After touching any module, update `docs/quality-document.md` with ratings (A/B/C/D) per dimension: test coverage, type safety, documentation, architecture compliance, performance.

## Verification Trail

`make vcr` / `npm run vcr` records a verification trail to `.harness/trails/` after check and check-arch pass. A plain `make check` does not write trails — only a successful VCR run does.

Record evidence in that feature's `.harness/state/<id>.json` under its `evidence` field.

## Session Report

```bash
make report        # or: npm run report [-- --days N] [-- --json]
```

Aggregates `.harness/traces/` into a session digest: per-session start/end, duration, open/closed state, active feature, decisions recorded, files modified, and feature evidence — plus totals for the window (default: last 7 days).

## Periodic Sweep

```bash
make sweep         # or: npm run sweep [-- --older-than N]
```

The sweep (default threshold: 30 days) archives old session traces to `.harness/traces/archive/`, prunes orphaned `.tmp` files from interrupted merges and stale open session records, and reports structural drift against the manifest (report-only). It never touches instance state — feature list, docs, and decisions are never modified by a sweep.

## Product observability (feat-017)

The harness sections above trace development sessions. The product traces itself:

**Traces.** Every agent run is a trace keyed `owner/name#number`. `Tracer` (`services/tracing.py`) records one span per graph node (`guard`, `triage`, `retrieve`, `decide`, `draft_*`, `human_gate`, `finalize`) with duration and status (`ok`, `error`, or `interrupted` when the run pauses at the human gate), and one span per model call with step, model, tokens, cost and latency, attached to the node that made it through a context variable. Spans are stored in `trace_spans` (migration `0005`) and served at `GET /api/traces/{owner}/{name}/{number}`. Model calls outside an agent run (evaluations) stay in the `CallLedger` summaries written by each evaluation command.

**KPIs** (`GET /api/kpis`, shown as a strip above the review queue):

| KPI | Source | What it tells you |
|---|---|---|
| Pending / approval rate / edit rate | `triage_runs`, `review_feedback` | How much reviewers accept as is, edit or reject |
| Escalation rate | traces: runs whose triage node made an LLM call | How often the trained classifier was unsure (D-012) |
| Cost per issue | traces: LLM span cost per run | Spend per triaged issue (0 on the fake provider) |
| Latency p50 / p95 | traces: node durations per run, human gate excluded | Agent time a maintainer waits for |
| Errors | traces: spans with status `error` | Failed nodes or model calls |
| Triage macro-F1, duplicate recall@10, answer faithfulness, replay agreement | committed `evals/results/*.json` | The offline quality the live numbers should be read against |

Missing measurements are reported as `null` (a dash in the UI), never as zero.
