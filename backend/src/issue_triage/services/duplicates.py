"""Use cases: embed issues, find similar earlier issues, and score duplicate detection."""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Protocol

from issue_triage.domain.docs import Embedder
from issue_triage.domain.evaluation import DuplicateCase
from issue_triage.domain.issues import Issue
from issue_triage.domain.search import (
    SEARCH_MODES,
    IssueKeywordIndex,
    IssueVectorIndex,
    SearchMode,
    reciprocal_rank_fusion,
)
from issue_triage.services.docs_index import issue_query


class IssueEmbeddingStore(Protocol):
    def upsert(
        self, repository: str, numbers: Sequence[int], embeddings: Sequence[Sequence[float]]
    ) -> int: ...


def embed_issues(
    issues: Sequence[Issue],
    embedder: Embedder,
    store: IssueEmbeddingStore,
    *,
    batch_size: int = 256,
    on_batch: Callable[[int], None] | None = None,
) -> int:
    done = 0
    for start in range(0, len(issues), batch_size):
        batch = issues[start : start + batch_size]
        vectors = embedder.embed([issue_query(i.title, i.body) for i in batch])
        store.upsert(batch[0].repository, [i.number for i in batch], vectors)
        done += len(batch)
        if on_batch:
            on_batch(done)
    return done


class SimilarIssueFinder:
    def __init__(
        self,
        repository: str,
        embedder: Embedder,
        vectors: IssueVectorIndex,
        keywords: IssueKeywordIndex,
        *,
        candidates: int = 50,
    ) -> None:
        self.repository = repository
        self._embedder = embedder
        self._vectors = vectors
        self._keywords = keywords
        self._candidates = candidates

    def find(
        self,
        title: str,
        body: str,
        *,
        before: datetime,
        exclude: int | None = None,
        k: int = 10,
        mode: SearchMode = "hybrid",
        embedding: Sequence[float] | None = None,
    ) -> list[int]:
        """Issue numbers created before ``before``, most similar first."""
        query = issue_query(title, body)
        rankings: list[list[int]] = []
        if mode in ("vector", "hybrid"):
            vector = embedding if embedding is not None else self._embedder.embed([query])[0]
            hits = self._vectors.search(
                self.repository, vector, before=before, exclude=exclude, k=self._candidates
            )
            rankings.append([number for number, _ in hits])
        if mode in ("keyword", "hybrid"):
            hits = self._keywords.search(query, before=before, exclude=exclude, k=self._candidates)
            rankings.append([number for number, _ in hits])
        if mode == "hybrid":
            return reciprocal_rank_fusion(rankings, limit=k)
        return rankings[0][:k]


def evaluate_duplicates(
    cases: Sequence[DuplicateCase],
    finder: SimilarIssueFinder,
    embedder: Embedder,
    *,
    modes: Sequence[SearchMode] = SEARCH_MODES,
    k: int = 10,
) -> list[dict[str, Any]]:
    """recall@1/5/k and MRR@k of the maintainer-linked original, per search mode."""
    vectors = embedder.embed([issue_query(c.title, c.body) for c in cases])
    results = []
    for mode in modes:
        ranks: list[int | None] = []
        for case, vector in zip(cases, vectors, strict=True):
            found = finder.find(
                case.title,
                case.body,
                before=case.as_of,
                exclude=case.number,
                k=k,
                mode=mode,
                embedding=vector,
            )
            ranks.append(
                found.index(case.original_number) + 1 if case.original_number in found else None
            )
        n = len(cases)

        def recall(cutoff: int, ranks: list[int | None] = ranks, n: int = n) -> float:
            return (
                round(sum(1 for r in ranks if r is not None and r <= cutoff) / n, 4) if n else 0.0
            )

        results.append(
            {
                "mode": mode,
                "n": n,
                "recall_at_1": recall(1),
                "recall_at_5": recall(5),
                f"recall_at_{k}": recall(k),
                f"mrr_at_{k}": round(sum(1 / r for r in ranks if r) / n, 4) if n else 0.0,
            }
        )
    return results
