"""Use cases: embed issues, find similar earlier issues, and score duplicate detection."""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from issue_triage.domain.docs import Embedder
from issue_triage.domain.evaluation import DuplicateCase
from issue_triage.domain.issues import Issue
from issue_triage.domain.search import (
    SEARCH_MODES,
    IssueKeywordIndex,
    IssueVectorIndex,
    Reranker,
    SearchMode,
    reciprocal_rank_fusion,
)
from issue_triage.services.docs_index import issue_query
from issue_triage.services.query_rewrite import QueryRewriter


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
        rewriter: QueryRewriter | None = None,
        reranker: Reranker | None = None,
        candidate_text: Callable[[int], str] | None = None,
        rerank_top: int = 20,
    ) -> None:
        self.repository = repository
        self._embedder = embedder
        self._vectors = vectors
        self._keywords = keywords
        self._candidates = candidates
        self._rewriter = rewriter
        self._reranker = reranker
        self._candidate_text = candidate_text
        self._rerank_top = rerank_top

    def _rankings(
        self,
        queries: Sequence[str],
        vectors: Sequence[Sequence[float]],
        mode: SearchMode,
        before: datetime,
        exclude: int | None,
    ) -> list[list[int]]:
        rankings: list[list[int]] = []
        for query, vector in zip(queries, vectors, strict=True):
            if mode in ("vector", "hybrid"):
                hits = self._vectors.search(
                    self.repository, vector, before=before, exclude=exclude, k=self._candidates
                )
                rankings.append([number for number, _ in hits])
            if mode in ("keyword", "hybrid"):
                hits = self._keywords.search(
                    query, before=before, exclude=exclude, k=self._candidates
                )
                rankings.append([number for number, _ in hits])
        return rankings

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
        rewrite: bool = False,
        rerank: bool = False,
    ) -> list[int]:
        """Issue numbers created before ``before``, most similar first.

        ``rewrite`` adds LLM-reformulated queries (fused with the original by RRF);
        ``rerank`` re-scores the top ``rerank_top`` fused candidates with the reranker.
        """
        query = issue_query(title, body)
        queries = [query]
        if rewrite and self._rewriter is not None:
            queries += self._rewriter.rewrite(title, body)
        vectors: list[Sequence[float]] = []
        if mode in ("vector", "hybrid"):
            first = embedding if embedding is not None else self._embedder.embed([query])[0]
            vectors = [first, *self._embedder.embed(queries[1:])] if len(queries) > 1 else [first]
        else:
            vectors = [[] for _ in queries]
        rankings = self._rankings(queries, vectors, mode, before, exclude)
        limit = max(k, self._rerank_top) if rerank else k
        fused = (
            reciprocal_rank_fusion(rankings, limit=limit)
            if len(rankings) > 1
            else rankings[0][:limit]
        )
        if rerank and self._reranker is not None and self._candidate_text is not None:
            head, tail = fused[: self._rerank_top], fused[self._rerank_top :]
            scores = self._reranker.score(query, [self._candidate_text(n) for n in head])
            order = sorted(range(len(head)), key=lambda i: -scores[i])
            fused = [head[i] for i in order] + tail
        return fused[:k]


@dataclass(frozen=True)
class SearchVariant:
    name: str
    mode: SearchMode = "hybrid"
    rewrite: bool = False
    rerank: bool = False


BASE_VARIANTS = tuple(SearchVariant(mode, mode) for mode in SEARCH_MODES)
RERANK_VARIANTS = (
    SearchVariant("hybrid"),
    SearchVariant("hybrid+rewrite", rewrite=True),
    SearchVariant("hybrid+rerank", rerank=True),
    SearchVariant("hybrid+rewrite+rerank", rewrite=True, rerank=True),
)


def evaluate_duplicates(
    cases: Sequence[DuplicateCase],
    finder: SimilarIssueFinder,
    embedder: Embedder,
    *,
    variants: Sequence[SearchVariant] = BASE_VARIANTS,
    k: int = 10,
) -> list[dict[str, Any]]:
    """recall@1/5/k, MRR@k of the maintainer-linked original and latency, per variant."""
    vectors = embedder.embed([issue_query(c.title, c.body) for c in cases])
    n = len(cases)
    results = []
    for variant in variants:
        ranks: list[int | None] = []
        latencies: list[float] = []
        for case, vector in zip(cases, vectors, strict=True):
            start = time.perf_counter()
            found = finder.find(
                case.title,
                case.body,
                before=case.as_of,
                exclude=case.number,
                k=k,
                mode=variant.mode,
                embedding=vector,
                rewrite=variant.rewrite,
                rerank=variant.rerank,
            )
            latencies.append((time.perf_counter() - start) * 1000)
            ranks.append(
                found.index(case.original_number) + 1 if case.original_number in found else None
            )

        def recall(cutoff: int, ranks: list[int | None] = ranks) -> float:
            hits = sum(1 for r in ranks if r is not None and r <= cutoff)
            return round(hits / n, 4) if n else 0.0

        latencies.sort()
        results.append(
            {
                "mode": variant.name,
                "n": n,
                "recall_at_1": recall(1),
                "recall_at_5": recall(5),
                f"recall_at_{k}": recall(k),
                f"mrr_at_{k}": round(sum(1 / r for r in ranks if r) / n, 4) if n else 0.0,
                "latency_ms_p50": round(latencies[n // 2], 1) if n else 0.0,
            }
        )
    return results
