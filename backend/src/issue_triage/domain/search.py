"""Similar-issue search: the interfaces and rank fusion (feat-009).

Every search takes ``before``: only issues created strictly earlier are candidates, so a
replayed issue can never find something that did not exist yet (D-004).
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Protocol

SearchMode = Literal["vector", "keyword", "hybrid"]
SEARCH_MODES: tuple[SearchMode, ...] = ("vector", "keyword", "hybrid")
RRF_K = 60


class IssueVectorIndex(Protocol):
    def search(
        self,
        repository: str,
        embedding: Sequence[float],
        *,
        before: datetime,
        exclude: int | None = None,
        k: int = 10,
    ) -> list[tuple[int, float]]:
        """(issue number, cosine similarity), best first."""
        ...


class IssueKeywordIndex(Protocol):
    def search(
        self, query: str, *, before: datetime, exclude: int | None = None, k: int = 10
    ) -> list[tuple[int, float]]:
        """(issue number, BM25 score), best first."""
        ...


class Reranker(Protocol):
    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        """Relevance of each text to the query; higher is better."""
        ...


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[int]], *, k: int = RRF_K, limit: int = 10
) -> list[int]:
    """Fuse ranked lists by summing 1 / (k + rank); robust to incomparable score scales."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda item: (-scores[item], item))[:limit]
