"""Classification metrics. Macro-F1 is the headline: uv's classes are imbalanced (D-001)."""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ClassScores:
    precision: float
    recall: float
    f1: float
    support: int


@dataclass(frozen=True)
class ClassificationReport:
    n: int
    accuracy: float
    macro_f1: float
    per_class: dict[str, ClassScores]
    confusion: dict[str, dict[str, int]]  # confusion[expected][predicted]


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def classification_report(
    expected: Sequence[str], predicted: Sequence[str], classes: Sequence[str]
) -> ClassificationReport:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted differ in length")
    confusion = {e: {p: 0 for p in classes} for e in classes}
    for e, p in zip(expected, predicted, strict=True):
        confusion[e][p] += 1
    per_class: dict[str, ClassScores] = {}
    for c in classes:
        tp = confusion[c][c]
        predicted_c = sum(confusion[e][c] for e in classes)
        support = sum(confusion[c].values())
        precision, recall = _ratio(tp, predicted_c), _ratio(tp, support)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[c] = ClassScores(round(precision, 4), round(recall, 4), round(f1, 4), support)
    correct = sum(confusion[c][c] for c in classes)
    return ClassificationReport(
        n=len(expected),
        accuracy=round(_ratio(correct, len(expected)), 4),
        macro_f1=round(sum(s.f1 for s in per_class.values()) / len(classes), 4),
        per_class=per_class,
        confusion=confusion,
    )
