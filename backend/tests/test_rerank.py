import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from issue_triage.domain.evaluation import DuplicateCase
from issue_triage.domain.issues import Issue
from issue_triage.domain.llm import Completion, Message, Usage
from issue_triage.infrastructure.embeddings import HashingEmbedder
from issue_triage.infrastructure.keyword_index import Bm25IssueIndex
from issue_triage.infrastructure.llm.fake_handlers import query_keyphrases
from issue_triage.infrastructure.reranker import CrossEncoderReranker, OverlapReranker
from issue_triage.services.duplicates import (
    RERANK_VARIANTS,
    SimilarIssueFinder,
    evaluate_duplicates,
)
from issue_triage.services.llm import LLMClient, ModelRouter
from issue_triage.services.prompts import load_prompt
from issue_triage.services.query_rewrite import QueryRewriter
from issue_triage.wiring import build_llm_client

from .helpers import IsolatedSettings

REPO = "acme/widgets"
T0 = datetime(2025, 1, 1, tzinfo=UTC)
BODY = (
    "Running `uv sync --frozen` in CI:\n"
    "error: Failed to download `torch==2.5.1`\n"
    "  Caused by: connection reset\n"
    "Thanks for uv!"
)


def issue(number: int, title: str, body: str) -> Issue:
    return Issue(
        REPO,
        number,
        title,
        body,
        "user-x",
        "NONE",
        "CLOSED",
        "COMPLETED",
        T0 + timedelta(days=number),
        None,
    )


ISSUES = [
    issue(1, "Download of torch fails in CI", "error: Failed to download torch==2.5.1 reset"),
    issue(2, "Feature: export without dev deps", "please add --no-dev to uv export"),
    issue(3, "uv sync --frozen slow", "uv sync --frozen takes minutes"),
    issue(4, "torch download fails with connection reset", BODY),
]


class ListVectors:
    def __init__(self) -> None:
        self.embedder = HashingEmbedder()
        texts = [f"{i.title}\n\n{i.body}" for i in ISSUES]
        self.rows = list(zip(ISSUES, self.embedder.embed(texts), strict=True))

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
            (i.number, sum(a * b for a, b in zip(v, embedding, strict=True)))
            for i, v in self.rows
            if i.created_at < before and i.number != exclude
        ]
        return sorted(scored, key=lambda r: -r[1])[:k]


def test_fake_rewriter_extracts_title_error_line_and_inline_code() -> None:
    messages = load_prompt("query_rewrite", "v1").render(
        title="torch download fails", body=f"<issue>\n{BODY}\n</issue>"
    )

    queries = json.loads(query_keyphrases(messages, None))["queries"]

    assert queries == [
        "torch download fails",
        "error: Failed to download `torch==2.5.1`",
        "uv sync --frozen torch==2.5.1",
    ]


def test_rewriter_returns_queries_through_the_llm_client() -> None:
    llm = build_llm_client(IsolatedSettings())

    queries = QueryRewriter(llm).rewrite("torch download fails", BODY)

    assert len(queries) == 3 and queries[0] == "torch download fails"
    assert llm.ledger.records[0].step == "query_rewrite"
    assert llm.ledger.records[0].model == "fake/query-keyphrases"


class Garbage:
    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        response_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        return Completion("no json", model, Usage(1, 1, 0.0, 1.0))


def test_rewriter_degrades_to_no_extra_queries() -> None:
    rewriter = QueryRewriter(LLMClient(Garbage(), ModelRouter({}, "x/y")))

    assert rewriter.rewrite("t", "b") == []


def test_overlap_reranker_prefers_shared_terms() -> None:
    scores = OverlapReranker().score("torch download reset", ["torch download reset", "uv export"])

    assert scores[0] > scores[1] == 0.0
    assert CrossEncoderReranker("unused-model").score("q", []) == []  # no model load


def make_finder() -> SimilarIssueFinder:
    vectors = ListVectors()
    texts = {i.number: f"{i.title}\n\n{i.body}" for i in ISSUES}
    return SimilarIssueFinder(
        REPO,
        vectors.embedder,
        vectors,
        Bm25IssueIndex(ISSUES),
        rewriter=QueryRewriter(build_llm_client(IsolatedSettings())),
        reranker=OverlapReranker(),
        candidate_text=texts.__getitem__,
        rerank_top=3,
    )


def test_rewrite_and_rerank_keep_the_as_of_filter() -> None:
    finder = make_finder()
    target = ISSUES[3]

    for rewrite in (False, True):
        for rerank in (False, True):
            found = finder.find(
                target.title,
                target.body,
                before=target.created_at,
                exclude=4,
                rewrite=rewrite,
                rerank=rerank,
            )
            assert found and found[0] == 1
            assert all(n < 4 for n in found)


def test_reranking_reorders_only_the_head() -> None:
    class Reverse:
        def score(self, query: str, texts: Sequence[str]) -> list[float]:
            return [float(-i) for i in range(len(texts))][::-1]  # last candidate scores best

    finder = make_finder()
    finder._reranker = Reverse()  # noqa: SLF001
    base = finder.find("torch", "download", before=T0 + timedelta(days=10), k=3)
    reranked = finder.find("torch", "download", before=T0 + timedelta(days=10), k=3, rerank=True)

    assert sorted(base) == sorted(reranked) and reranked == base[::-1]


def test_rerank_variants_report_quality_and_latency() -> None:
    cases = [
        DuplicateCase(REPO, 4, ISSUES[3].created_at, ISSUES[3].title, BODY, "test", 1, "comment")
    ]

    results = evaluate_duplicates(
        cases, make_finder(), HashingEmbedder(), variants=RERANK_VARIANTS, k=3
    )

    assert [r["mode"] for r in results] == [
        "hybrid",
        "hybrid+rewrite",
        "hybrid+rerank",
        "hybrid+rewrite+rerank",
    ]
    assert all(r["recall_at_1"] == 1.0 and r["latency_ms_p50"] >= 0 for r in results)
