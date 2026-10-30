import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from issue_triage.domain.answer_metrics import cosine, lexical_faithfulness, sentences
from issue_triage.domain.docs import DocChunk, DocPage
from issue_triage.domain.evaluation import AnswerCase
from issue_triage.domain.llm import Completion, Message, Usage
from issue_triage.infrastructure.embeddings import HashingEmbedder
from issue_triage.infrastructure.llm.fake_handlers import answer_extractive, judge_overlap
from issue_triage.services.answer_eval import Judge, evaluate_answers
from issue_triage.services.answers import AnswerDrafter, DocRetriever, chunk_id, format_context
from issue_triage.services.docs_index import index_docs
from issue_triage.services.llm import LLMClient, ModelRouter
from issue_triage.services.prompts import load_prompt
from issue_triage.wiring import build_llm_client

from .helpers import IsolatedSettings

SOURCE = "acme/widgets"
BASE = "https://docs.astral.sh/uv/"
PAGES = [
    DocPage(
        SOURCE,
        "concepts/cache.md",
        "Caching",
        "# Caching\n\n## Cache directory\n\nSet UV_CACHE_DIR to move the cache. "
        "The default lives in the user cache folder.",
    ),
    DocPage(
        SOURCE,
        "concepts/python-versions.md",
        "Python versions",
        "# Python versions\n\nPin a version with uv python pin. It writes .python-version.",
    ),
]


class MemoryIndex:
    def __init__(self) -> None:
        self.rows: list[tuple[DocChunk, list[float]]] = []

    def replace(
        self,
        source: str,
        chunker: str,
        commit: str,
        chunks: Sequence[DocChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> int:
        self.rows = [(c, list(e)) for c, e in zip(chunks, embeddings, strict=True)]
        return len(chunks)

    def search(
        self, source: str, chunker: str, embedding: Sequence[float], k: int = 5
    ) -> list[tuple[DocChunk, float]]:
        scored = [(c, cosine(e, embedding)) for c, e in self.rows]
        return sorted(scored, key=lambda r: -r[1])[:k]

    def count(self, source: str, chunker: str) -> int:
        return len(self.rows)


@pytest.fixture
def retriever() -> DocRetriever:
    embedder = HashingEmbedder()
    index = MemoryIndex()
    index_docs(PAGES, "heading", embedder, index, "abc")
    return DocRetriever(embedder, index, SOURCE, k=2)


class Replying:
    """Provider returning a fixed draft and recording the prompt it was sent."""

    def __init__(self, draft: dict[str, Any]) -> None:
        self.draft = draft
        self.seen: list[list[Message]] = []

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        response_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        self.seen.append(list(messages))
        return Completion(json.dumps(self.draft), model, Usage(200, 40, 0.0, 10.0))


def drafter_with(draft: dict[str, Any], retriever: DocRetriever) -> tuple[AnswerDrafter, Replying]:
    provider = Replying(draft)
    return AnswerDrafter(LLMClient(provider, ModelRouter({}, "fake/x")), retriever), provider


def test_citations_must_point_at_retrieved_chunks(retriever: DocRetriever) -> None:
    drafter, provider = drafter_with(
        {
            "reply": "Set UV_CACHE_DIR [concepts/cache#0]",
            "citations": ["concepts/cache#0", "reference/cli#9"],
            "confidence": 0.9,
        },
        retriever,
    )

    result = drafter.draft("Move the cache?", "How do I change the UV_CACHE_DIR cache folder")

    assert [chunk_id(c) for c in result.citations] == ["concepts/cache#0"]
    assert result.invalid_citations == ["reference/cli#9"]
    assert result.needs_human  # an invented citation always goes to a human
    assert "[concepts/cache#0]" in provider.seen[0][1].content


def test_grounded_confident_draft_does_not_need_a_human(retriever: DocRetriever) -> None:
    drafter, _ = drafter_with(
        {
            "reply": "Use uv python pin.",
            "citations": ["concepts/python-versions#0"],
            "confidence": 0.8,
        },
        retriever,
    )

    result = drafter.draft("Pin python", "how do I pin a python version")

    assert not result.needs_human and result.citations[0].page == "concepts/python-versions"


@pytest.mark.parametrize(
    "draft",
    [
        {"reply": "No idea.", "citations": [], "confidence": 0.9},  # cites nothing
        {
            "reply": "Use uv python pin.",
            "citations": ["concepts/python-versions#0"],
            "confidence": 0.2,
        },
    ],
)
def test_ungrounded_or_unsure_drafts_need_a_human(
    retriever: DocRetriever, draft: dict[str, Any]
) -> None:
    drafter, _ = drafter_with(draft, retriever)

    assert drafter.draft("Pin python", "how do I pin a python version").needs_human


def test_secrets_never_reach_the_reply(retriever: DocRetriever) -> None:
    token = "ghp_" + "Q1" * 18
    drafter, _ = drafter_with(
        {
            "reply": f"Try UV_INDEX_TOKEN={token}",
            "citations": ["concepts/cache#0"],
            "confidence": 0.9,
        },
        retriever,
    )

    assert token not in drafter.draft("Index auth", "token problems").reply


def test_fake_answer_handler_quotes_and_cites_the_top_excerpt(retriever: DocRetriever) -> None:
    chunks = retriever.retrieve("cache folder", "UV_CACHE_DIR")
    messages = load_prompt("answer", "v1").render(
        context=format_context(chunks), title="cache folder", body="<issue>\nwhere\n</issue>"
    )

    draft = json.loads(answer_extractive(messages, None))

    assert draft["citations"] == [chunk_id(chunks[0])]
    assert "UV_CACHE_DIR" in draft["reply"]


def test_fake_judge_scales_overlap() -> None:
    def grade(draft: str) -> int:
        messages = load_prompt("judge", "v1").render(
            question="q", reference="Set UV_CACHE_DIR to move the cache folder", draft=draft
        )
        return int(json.loads(judge_overlap(messages, None))["score"])

    assert grade("Set UV_CACHE_DIR to move the cache folder") == 5
    assert grade("unrelated words entirely") == 1


def test_lexical_faithfulness_counts_supported_sentences() -> None:
    cited = ["Set UV_CACHE_DIR to move the cache. The default lives in the user cache folder."]

    assert lexical_faithfulness("Set UV_CACHE_DIR to move the cache.", cited) == 1.0
    assert (
        lexical_faithfulness("Set UV_CACHE_DIR to move the cache. Bananas grow quickly.", cited)
        == 0.5
    )
    assert lexical_faithfulness("", cited) == 0.0
    assert sentences("One two three. Four five six!") == ["One two three.", "Four five six!"]


def test_answer_evaluation_end_to_end_with_fakes(retriever: DocRetriever) -> None:
    llm = build_llm_client(IsolatedSettings())
    as_of = datetime(2025, 9, 20, tzinfo=UTC)
    cases = [
        AnswerCase(
            SOURCE,
            1,
            as_of,
            "Move the cache?",
            "UV_CACHE_DIR cache folder",
            "test",
            f"Set UV_CACHE_DIR, see {BASE}concepts/cache/",
        ),
        AnswerCase(
            SOURCE, 2, as_of, "Pin python", "pin a python version", "test", "Run uv python pin 3.12"
        ),
    ]

    summary = evaluate_answers(
        cases,
        AnswerDrafter(llm, retriever),
        Judge(llm),
        HashingEmbedder(),
        llm.ledger,
        docs_base_url=BASE,
    )

    assert summary["drafted"] == 2 and summary["failures"] == 0
    assert summary["citation_validity"] == 1.0
    assert summary["cases_with_maintainer_link"] == 1
    assert summary["cited_page_matches_maintainer_link"] == 1.0
    assert 1 <= summary["judge_score_mean"] <= 5 and summary["judge_scored"] == 2
    assert 0 < summary["faithfulness_lexical"] <= 1
    assert {"answer", "judge"} <= set(summary["llm"]["by_step"])
