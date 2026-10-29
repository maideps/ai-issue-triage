from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from issue_triage.domain.evaluation import DuplicateCase
from issue_triage.domain.issues import Issue
from issue_triage.domain.search import reciprocal_rank_fusion
from issue_triage.infrastructure.db.issue_vectors import PostgresIssueVectorIndex
from issue_triage.infrastructure.db.issues import PostgresIssueRepository
from issue_triage.infrastructure.embeddings import HashingEmbedder
from issue_triage.infrastructure.keyword_index import Bm25IssueIndex, tokenize
from issue_triage.services.duplicates import SimilarIssueFinder, embed_issues, evaluate_duplicates

REPO = "acme/widgets"
T0 = datetime(2025, 1, 1, tzinfo=UTC)
TEXTS = {
    1: ("Resolver panics on Windows", "error: failed to resolve torch==2.5.1+cu121 on win_amd64"),
    2: ("Cache directory permissions", "UV_CACHE_DIR is not writable inside docker"),
    3: ("Add --no-dev flag to uv export", "would like to exclude dev dependencies"),
    4: ("Resolver panic on Windows with torch", "failed to resolve torch==2.5.1+cu121 win_amd64"),
    5: ("Docker cache not writable", "UV_CACHE_DIR permission denied in container"),
}


def issue(number: int) -> Issue:
    title, body = TEXTS[number]
    return Issue(
        repository=REPO,
        number=number,
        title=title,
        body=body,
        author="user-x",
        author_association="NONE",
        state="CLOSED",
        state_reason="COMPLETED",
        created_at=T0 + timedelta(days=number),
        closed_at=None,
    )


ISSUES = [issue(n) for n in TEXTS]


def test_rrf_rewards_agreement_between_rankings() -> None:
    fused = reciprocal_rank_fusion([[1, 2, 3], [3, 1, 4]], limit=3)

    assert fused[0] == 1  # ranked 1st and 2nd beats 3rd and 1st
    assert set(fused) == {1, 3, 2}


def test_tokenizer_keeps_flags_versions_and_env_vars() -> None:
    tokens = tokenize("uv export --no-dev fails: torch==2.5.1+cu121 and UV_CACHE_DIR")

    assert {"export", "no-dev", "fails", "torch", "2.5.1", "cu121", "uv_cache_dir"} <= set(tokens)
    assert "uv" not in tokens and "and" not in tokens


def test_bm25_respects_as_of_and_exclusion() -> None:
    index = Bm25IssueIndex(ISSUES)
    query = "failed to resolve torch==2.5.1+cu121 win_amd64"

    everything = index.search(query, before=T0 + timedelta(days=10), exclude=4)
    only_early = index.search(query, before=T0 + timedelta(days=1, hours=1))

    assert everything[0][0] == 1 and 4 not in [n for n, _ in everything]
    assert [n for n, _ in only_early] == [1]
    assert index.search("", before=T0 + timedelta(days=10)) == []


class ListVectorIndex:
    """In-memory vector index over the hashing embedder, with the as-of filter."""

    def __init__(self, issues: Sequence[Issue]) -> None:
        self.embedder = HashingEmbedder()
        self.rows = [
            (i.number, i.created_at, v)
            for i, v in zip(
                issues, self.embedder.embed([f"{i.title}\n\n{i.body}" for i in issues]), strict=True
            )
        ]

    def search(
        self,
        repository: str,
        embedding: Sequence[float],
        *,
        before: datetime,
        exclude: int | None = None,
        k: int = 10,
    ) -> list[tuple[int, float]]:
        scored = [
            (n, sum(a * b for a, b in zip(v, embedding, strict=True)))
            for n, created, v in self.rows
            if created < before and n != exclude
        ]
        return sorted(scored, key=lambda r: -r[1])[:k]


@pytest.fixture
def finder() -> SimilarIssueFinder:
    vectors = ListVectorIndex(ISSUES)
    return SimilarIssueFinder(REPO, vectors.embedder, vectors, Bm25IssueIndex(ISSUES))


@pytest.mark.parametrize("mode", ["vector", "keyword", "hybrid"])
def test_finder_returns_only_earlier_issues(finder: SimilarIssueFinder, mode: str) -> None:
    title, body = TEXTS[4]

    found = finder.find(title, body, before=ISSUES[3].created_at, exclude=4, mode=mode)  # type: ignore[arg-type]

    assert found[0] == 1
    assert all(n < 4 for n in found)


def test_duplicate_evaluation_reports_each_mode(finder: SimilarIssueFinder) -> None:
    cases = [
        DuplicateCase(REPO, 4, ISSUES[3].created_at, *TEXTS[4], "test", 1, "duplicateOf"),
        DuplicateCase(REPO, 5, ISSUES[4].created_at, *TEXTS[5], "test", 2, "comment"),
    ]

    results = evaluate_duplicates(cases, finder, HashingEmbedder(), k=3)

    assert [r["mode"] for r in results] == ["vector", "keyword", "hybrid"]
    for r in results:
        assert r["n"] == 2
        assert r["recall_at_1"] <= r["recall_at_5"] and r["recall_at_3"] <= r["recall_at_5"]
    hybrid = results[2]
    assert hybrid["recall_at_3"] == 1.0 and hybrid["mrr_at_3"] > 0.5


def test_pgvector_issue_index_filters_by_creation_time(sessions: sessionmaker[Session]) -> None:
    PostgresIssueRepository(sessions).upsert(ISSUES)
    index = PostgresIssueVectorIndex(sessions)
    embedder = HashingEmbedder()

    stored = embed_issues(ISSUES, embedder, index, batch_size=2)
    query = embedder.embed([f"{TEXTS[4][0]}\n\n{TEXTS[4][1]}"])[0]
    early = index.search(REPO, query, before=ISSUES[3].created_at, exclude=4, k=10)

    assert stored == index.count(REPO) == 5
    assert early[0][0] == 1 and {n for n, _ in early} == {1, 2, 3}
    # Re-embedding replaces rows instead of duplicating them.
    embed_issues([replace(ISSUES[0], body="changed")], embedder, index)
    assert index.count(REPO) == 5


def test_embedding_unknown_issues_fails_loudly(sessions: sessionmaker[Session]) -> None:
    with pytest.raises(ValueError, match="not loaded"):
        PostgresIssueVectorIndex(sessions).upsert(REPO, [999], [[0.0] * 384])
