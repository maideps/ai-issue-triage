"""A small trained classifier for type triage that escalates to the LLM when unsure.

The decision "bug, question, enhancement or other?" is a choice among known options, so a
linear model over the issue embeddings handles the confident cases in microseconds for no
token cost; only low-confidence issues go to the LLM step (the ebook's decision 4).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import numpy as np
from sklearn.linear_model import LogisticRegression

from issue_triage.services.docs_index import issue_query
from issue_triage.services.guardrails import guard_input
from issue_triage.services.triage import TriageDecision

Source = Literal["classifier", "llm"]


class EmbeddingClassifier:
    def __init__(self, c: float = 1.0) -> None:
        self._model = LogisticRegression(C=c, max_iter=2000, class_weight="balanced")

    def fit(
        self, vectors: Sequence[Sequence[float]], labels: Sequence[str]
    ) -> "EmbeddingClassifier":
        self._model.fit(np.asarray(vectors, dtype=np.float32), list(labels))
        return self

    @property
    def classes(self) -> list[str]:
        return [str(c) for c in self._model.classes_]

    def predict(self, vectors: Sequence[Sequence[float]]) -> list[tuple[str, float]]:
        """(label, probability of that label) per vector."""
        probabilities = self._model.predict_proba(np.asarray(vectors, dtype=np.float32))
        classes = self.classes
        return [(classes[int(row.argmax())], float(row.max())) for row in probabilities]


@dataclass(frozen=True)
class RoutedLabel:
    label: str
    confidence: float
    source: Source


def route(
    prediction: tuple[str, float], threshold: float, ask_llm: Callable[[], str]
) -> RoutedLabel:
    label, confidence = prediction
    if confidence >= threshold:
        return RoutedLabel(label, confidence, "classifier")
    return RoutedLabel(ask_llm(), confidence, "llm")


def escalation_curve(
    expected: Sequence[str],
    predictions: Sequence[tuple[str, float]],
    llm_labels: Sequence[str],
    thresholds: Sequence[float],
    score: Callable[[Sequence[str], Sequence[str]], float],
) -> list[dict[str, Any]]:
    """Macro-F1 and escalation rate per threshold, using precomputed LLM answers."""
    rows = []
    for threshold in thresholds:
        routed = [
            label if confidence >= threshold else llm
            for (label, confidence), llm in zip(predictions, llm_labels, strict=True)
        ]
        escalated = sum(1 for _, confidence in predictions if confidence < threshold)
        rows.append(
            {
                "threshold": threshold,
                "macro_f1": round(score(expected, routed), 4),
                "escalation_rate": round(escalated / len(expected), 4) if expected else 0.0,
            }
        )
    return rows


class TypeClassifier(Protocol):
    def classify(self, title: str, body: str) -> TriageDecision: ...


class EscalatingTriager:
    """Type triage for the agent: the classifier when confident, the LLM step otherwise."""

    def __init__(
        self,
        classifier: EmbeddingClassifier,
        embed: Callable[[Sequence[str]], list[list[float]]],
        llm_triager: TypeClassifier,
        threshold: float = 0.5,
    ) -> None:
        self._classifier = classifier
        self._embed = embed
        self._llm = llm_triager
        self._threshold = threshold

    def classify(self, title: str, body: str) -> TriageDecision:
        vector = self._embed([issue_query(title, body)])[0]
        label, confidence = self._classifier.predict([vector])[0]
        guarded = guard_input(title, body)
        if confidence >= self._threshold:
            return TriageDecision(
                label,
                confidence,
                f"classifier {confidence:.2f}",
                guarded.flagged,
                "classifier",
                "logreg-embeddings",
            )
        return self._llm.classify(title, body)
