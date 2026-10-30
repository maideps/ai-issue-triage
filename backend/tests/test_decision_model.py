import random
from collections.abc import Sequence

import pytest
from sqlalchemy.orm import Session, sessionmaker

from issue_triage.domain.evaluation import TYPE_CLASSES
from issue_triage.domain.metrics import classification_report
from issue_triage.infrastructure.db.issue_vectors import PostgresIssueVectorIndex
from issue_triage.infrastructure.db.issues import PostgresIssueRepository
from issue_triage.infrastructure.embeddings import HashingEmbedder
from issue_triage.services.decision_model import (
    EmbeddingClassifier,
    EscalatingTriager,
    escalation_curve,
    route,
)
from issue_triage.services.duplicates import embed_issues
from issue_triage.services.triage import TriageDecision

from .test_duplicates import ISSUES, REPO

TEXTS = {
    "bug": "error panic crash failed traceback broken",
    "question": "how do i what is the way to configure",
    "enhancement": "please add support option feature request",
    "other": "documentation typo docs page wording",
}


def synthetic(n_per_class: int, seed: int) -> tuple[list[list[float]], list[str]]:
    rng = random.Random(seed)
    embedder = HashingEmbedder(dimension=64)
    texts, labels = [], []
    for label, words in TEXTS.items():
        for _ in range(n_per_class):
            sample = rng.sample(words.split(), 3) + [rng.choice(["uv", "pip", "lock", "sync"])]
            texts.append(" ".join(sample))
            labels.append(label)
    return embedder.embed(texts), labels


def macro(expected: Sequence[str], predicted: Sequence[str]) -> float:
    return classification_report(expected, predicted, TYPE_CLASSES).macro_f1


def test_classifier_learns_separable_classes() -> None:
    x_train, y_train = synthetic(30, seed=1)
    x_test, y_test = synthetic(10, seed=2)

    classifier = EmbeddingClassifier().fit(x_train, y_train)
    predictions = classifier.predict(x_test)

    assert sorted(classifier.classes) == sorted(TYPE_CLASSES)
    assert macro(y_test, [label for label, _ in predictions]) > 0.8
    assert all(0.25 <= confidence <= 1.0 for _, confidence in predictions)


def test_route_escalates_only_below_the_threshold() -> None:
    asked: list[int] = []

    def ask() -> str:
        asked.append(1)
        return "question"

    confident = route(("bug", 0.9), 0.5, ask)
    unsure = route(("bug", 0.4), 0.5, ask)

    assert (confident.label, confident.source) == ("bug", "classifier")
    assert (unsure.label, unsure.source, unsure.confidence) == ("question", "llm", 0.4)
    assert len(asked) == 1


def test_escalation_curve_spans_classifier_only_to_llm_only() -> None:
    expected = ["bug", "question", "bug", "other"]
    predictions = [("bug", 0.9), ("bug", 0.4), ("bug", 0.6), ("other", 0.2)]
    llm = ["question", "question", "enhancement", "other"]

    curve = escalation_curve(expected, predictions, llm, [0.0, 0.5, 1.01], macro)

    assert [r["escalation_rate"] for r in curve] == [0.0, 0.5, 1.0]
    assert curve[1]["macro_f1"] == pytest.approx(
        macro(expected, ["bug", "question", "bug", "other"])
    )
    assert curve[2]["macro_f1"] == pytest.approx(macro(expected, llm))


def test_stored_vectors_are_read_back_by_number(sessions: sessionmaker[Session]) -> None:
    PostgresIssueRepository(sessions).upsert(ISSUES)
    index = PostgresIssueVectorIndex(sessions)
    embed_issues(ISSUES, HashingEmbedder(), index)

    vectors = index.vectors(REPO, [1, 3, 999])

    assert set(vectors) == {1, 3}
    assert len(vectors[1]) == 384


class StubLLMTriager:
    def __init__(self) -> None:
        self.calls = 0

    def classify(self, title: str, body: str) -> TriageDecision:
        self.calls += 1
        return TriageDecision("question", 0.7, "llm", False, "triage@v1", "fake/x")


def test_escalating_triager_uses_the_llm_only_when_unsure() -> None:
    x_train, y_train = synthetic(30, seed=1)
    embedder = HashingEmbedder(dimension=64)
    llm = StubLLMTriager()
    triager = EscalatingTriager(
        EmbeddingClassifier().fit(x_train, y_train), embedder.embed, llm, 0.5
    )

    confident = triager.classify("panic crash", "error traceback failed broken")
    never = EscalatingTriager(
        EmbeddingClassifier().fit(x_train, y_train), embedder.embed, llm, 1.01
    )
    escalated = never.classify("anything", "at all")

    assert confident.model == "logreg-embeddings" and confident.label == "bug"
    assert escalated.label == "question" and llm.calls == 1
