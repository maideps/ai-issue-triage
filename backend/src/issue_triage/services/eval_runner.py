"""Run a pipeline step over an evaluation set and summarise quality, cost and latency.

Summaries hold metrics only, never issue text, so they can be committed (D-003).
"""

from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from issue_triage.domain.evaluation import TYPE_CLASSES, TypeCase
from issue_triage.domain.metrics import classification_report
from issue_triage.services.llm import CallLedger, StructuredOutputError
from issue_triage.services.triage import Triager

FAILED_PREDICTION = "other"  # a call that never returned valid JSON counts as a miss


def run_type_eval(
    cases: Sequence[TypeCase],
    triager: Triager,
    ledger: CallLedger,
    *,
    on_progress: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    expected: list[str] = []
    predicted: list[str] = []
    failures = needs_human = 0
    models: set[str] = set()
    for index, case in enumerate(cases, start=1):
        expected.append(case.label)
        try:
            decision = triager.classify(case.title, case.body)
        except StructuredOutputError:
            failures += 1
            predicted.append(FAILED_PREDICTION)
        else:
            predicted.append(decision.label)
            needs_human += decision.needs_human
            models.add(decision.model)
        if on_progress and index % 250 == 0:
            on_progress(index)

    report = classification_report(expected, predicted, TYPE_CLASSES)
    majority = max(TYPE_CLASSES, key=expected.count) if expected else TYPE_CLASSES[0]
    majority_report = classification_report(expected, [majority] * len(expected), TYPE_CLASSES)
    llm = ledger.summary()
    return {
        "task": "type",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "prompt": triager.prompt_ref,
        "models": sorted(models),
        "provider": "fake" if models and all(m.startswith("fake/") for m in models) else "real",
        "n": report.n,
        "accuracy": report.accuracy,
        "macro_f1": report.macro_f1,
        "majority_baseline": {
            "label": majority,
            "accuracy": majority_report.accuracy,
            "macro_f1": majority_report.macro_f1,
        },
        "per_class": {label: asdict(scores) for label, scores in report.per_class.items()},
        "confusion": report.confusion,
        "failures": failures,
        "needs_human": needs_human,
        "llm": llm,
        "cost_per_case_usd": round(llm["total"]["cost_usd"] / report.n, 6) if report.n else 0.0,
    }
