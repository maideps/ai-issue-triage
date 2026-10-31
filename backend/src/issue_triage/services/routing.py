"""Routing: which action the agent proposes, as a calibrated policy (feat-021, D-015).

The decision is a choice among four known actions, so it stays code (D-011), but its
thresholds are learned: a needs-info classifier trained on maintainers' ``needs-mre``
labels replaces the hand-written "no version, no code" rule, and the duplicate threshold
and precedence are picked by grid search on the newest slice of the train split.
"""

import itertools
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression

from issue_triage.domain.metrics import classification_report
from issue_triage.services.agent.state import ACTIONS, Action

NEVER = 1.01  # a threshold no similarity or probability reaches: the branch is off


@dataclass(frozen=True)
class RoutingSignals:
    """Everything the decision may look at, all known at ``as_of``."""

    triage_label: str
    best_similarity: float | None
    best_number: int | None
    needs_info_probability: float | None  # None when no needs-info model is loaded
    missing_info: bool  # the legacy heuristic, used when there is no model


@dataclass(frozen=True)
class RoutingPolicy:
    duplicate_threshold: float = 0.8
    # None keeps the legacy heuristic; a number uses the needs-info classifier.
    needs_info_threshold: float | None = None
    needs_info_labels: tuple[str, ...] = ("bug",)
    needs_info_first: bool = False  # ask for information before answering questions

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "RoutingPolicy":
        data: dict[str, Any] = json.loads(text)
        data["needs_info_labels"] = tuple(data["needs_info_labels"])
        return cls(**data)

    @classmethod
    def load(cls, path: Path) -> "RoutingPolicy | None":
        return cls.from_json(path.read_text(encoding="utf-8")) if path.exists() else None


LEGACY = RoutingPolicy()  # the hand-written rules from D-011


def _needs_info(signals: RoutingSignals, policy: RoutingPolicy) -> bool:
    if signals.triage_label not in policy.needs_info_labels:
        return False
    if policy.needs_info_threshold is None or signals.needs_info_probability is None:
        return signals.missing_info
    return signals.needs_info_probability >= policy.needs_info_threshold


def route(signals: RoutingSignals, policy: RoutingPolicy) -> tuple[Action, str]:
    """(action, reason). Precedence: duplicate, then needs-info / answer, then summary."""
    if (
        signals.best_similarity is not None
        and signals.best_number is not None
        and signals.best_similarity >= policy.duplicate_threshold
    ):
        return "duplicate", f"#{signals.best_number} similarity {signals.best_similarity}"
    needs_info = _needs_info(signals, policy)
    is_question = signals.triage_label == "question"
    reason_info = (
        f"needs-info probability {signals.needs_info_probability:.2f}"
        if signals.needs_info_probability is not None and policy.needs_info_threshold is not None
        else "report without version or reproduction"
    )
    if needs_info and (policy.needs_info_first or not is_question):
        return "needs_info", reason_info
    if is_question:
        return "answer", "question: answer from the docs"
    return "summary", f"{signals.triage_label}: summarise for a maintainer"


class NeedsInfoModel:
    """P(maintainers ask for a reproducible example), from the issue embedding."""

    def __init__(self, c: float = 1.0) -> None:
        self._model = LogisticRegression(C=c, max_iter=2000, class_weight="balanced")

    def fit(
        self, vectors: Sequence[Sequence[float]], needs_info: Sequence[bool]
    ) -> "NeedsInfoModel":
        self._model.fit(np.asarray(vectors, dtype=np.float32), [bool(v) for v in needs_info])
        return self

    def probability(self, vectors: Sequence[Sequence[float]]) -> list[float]:
        probabilities = self._model.predict_proba(np.asarray(vectors, dtype=np.float32))
        positive = list(self._model.classes_).index(True)
        return [float(row[positive]) for row in probabilities]


def action_report(expected: Sequence[str], predicted: Sequence[str]) -> dict[str, Any]:
    report = classification_report(expected, predicted, ACTIONS)
    return {
        "n": report.n,
        "macro_f1": report.macro_f1,
        "accuracy": report.accuracy,
        "recall": {a: report.per_class[a].recall for a in ACTIONS},
        "precision": {a: report.per_class[a].precision for a in ACTIONS},
    }


def policy_grid() -> list[RoutingPolicy]:
    duplicate = [0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, NEVER]
    needs_info: list[float | None] = [None, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, NEVER]
    labels = [("bug",), ("bug", "question", "enhancement", "other")]
    return [
        RoutingPolicy(d, n, lab, first)
        for d, n, lab, first in itertools.product(duplicate, needs_info, labels, (False, True))
    ]


def calibrate(
    signals: Sequence[RoutingSignals], expected: Sequence[str], grid: Sequence[RoutingPolicy]
) -> tuple[RoutingPolicy, list[dict[str, Any]]]:
    """Best policy by macro-F1 over the four actions; ties go to the legacy-closest one."""
    rows = []
    for policy in grid:
        predicted = [route(s, policy)[0] for s in signals]
        rows.append({"policy": asdict(policy), **action_report(expected, predicted)})
    rows.sort(key=lambda r: (-r["macro_f1"], -r["accuracy"]))
    best = rows[0]["policy"]
    return RoutingPolicy(**{**best, "needs_info_labels": tuple(best["needs_info_labels"])}), rows
