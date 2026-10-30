"""KPIs: what the running system does (live) and what the evaluations measured (offline)."""

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import quantiles
from typing import Any

from issue_triage.domain.reviews import RunRecord, RunStore
from issue_triage.domain.tracing import Span, TraceStore

GATE = "human_gate"  # waiting for a person is not system latency


def _percentiles(values: Sequence[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    if len(values) == 1:
        return round(values[0], 1), round(values[0], 1)
    cuts = quantiles(sorted(values), n=100, method="inclusive")
    return round(cuts[49], 1), round(cuts[94], 1)


def _ratio(part: float, whole: float) -> float | None:
    return round(part / whole, 4) if whole else None


def live_kpis(
    runs: Sequence[RunRecord], decisions: Mapping[str, int], spans: Sequence[Span]
) -> dict[str, Any]:
    status = {s: sum(1 for r in runs if r.status == s) for s in ("pending", "approved", "rejected")}
    approved_by_decision = decisions.get("approve", 0) + decisions.get("edit", 0)

    by_trace: dict[str, list[Span]] = defaultdict(list)
    for span in spans:
        by_trace[span.trace_id].append(span)
    latencies, costs = [], []
    with_triage = escalated = errors = llm_calls = 0
    for trace in by_trace.values():
        nodes = [s for s in trace if s.kind == "node" and s.name != GATE]
        calls = [s for s in trace if s.kind == "llm"]
        if nodes:
            latencies.append(sum(s.duration_ms for s in nodes))
            costs.append(sum(s.cost_usd for s in calls))
        if any(s.name == "triage" for s in nodes):
            with_triage += 1
            escalated += any(s.name == "triage" for s in calls)
        errors += sum(1 for s in trace if s.status == "error")
        llm_calls += len(calls)
    p50, p95 = _percentiles(latencies)
    return {
        "runs": len(runs),
        **status,
        "approval_rate": _ratio(status["approved"], status["approved"] + status["rejected"]),
        "edit_rate": _ratio(decisions.get("edit", 0), approved_by_decision),
        "escalation_rate": _ratio(escalated, with_triage),
        "traced_runs": len(by_trace),
        "llm_calls": llm_calls,
        "cost_per_issue_usd": round(sum(costs) / len(costs), 6) if costs else None,
        "latency_ms_p50": p50,
        "latency_ms_p95": p95,
        "errors": errors,
    }


def _load(results: Path, name: str) -> dict[str, Any] | None:
    path = results / name
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def offline_kpis(results: Path) -> dict[str, Any]:
    """Headline numbers from the committed evaluation summaries (None when not run)."""
    decision = _load(results, "decision-model-test.json")
    rerank = _load(results, "rerank-all.json")
    answers = _load(results, "answers-test.json")
    replay = _load(results, "replay-test.json")
    rewrite = next(
        (r for r in (rerank or {}).get("results", []) if r["mode"] == "hybrid+rewrite"), None
    )
    return {
        "triage_macro_f1": decision["test_escalating"]["macro_f1"] if decision else None,
        "triage_escalation_rate": decision["test_escalating"]["escalation_rate"]
        if decision
        else None,
        "duplicate_recall_at_10": rewrite["recall_at_10"] if rewrite else None,
        "answer_faithfulness": answers["faithfulness_lexical"] if answers else None,
        "answer_citation_validity": answers["citation_validity"] if answers else None,
        "replay_action_agreement": replay["action_agreement"]["rate"] if replay else None,
    }


class KpiService:
    def __init__(self, runs: RunStore, traces: TraceStore, results_dir: Path) -> None:
        self._runs = runs
        self._traces = traces
        self._results = results_dir

    def kpis(self) -> dict[str, Any]:
        return {
            "live": live_kpis(
                self._runs.list(limit=10_000), self._runs.decision_counts(), self._traces.spans()
            ),
            "offline": offline_kpis(self._results),
        }

    def trace(self, trace_id: str) -> Sequence[Span]:
        return self._traces.spans(trace_id)
