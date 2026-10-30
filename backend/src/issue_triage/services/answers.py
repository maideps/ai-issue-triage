"""Use case: draft a reply to a question issue, grounded in cited documentation chunks."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from issue_triage.domain.docs import Chunker, ChunkIndex, DocChunk, Embedder
from issue_triage.services.docs_index import issue_query
from issue_triage.services.guardrails import guard_input, guard_output
from issue_triage.services.llm import LLMClient
from issue_triage.services.prompts import Prompt, load_prompt

ANSWER_STEP = "answer"


class AnswerDraft(BaseModel):
    reply: str
    citations: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


def chunk_id(chunk: DocChunk) -> str:
    return f"{chunk.page}#{chunk.position}"


@dataclass(frozen=True)
class DraftResult:
    reply: str
    citations: list[DocChunk]
    confidence: float
    needs_human: bool
    retrieved: list[DocChunk]
    invalid_citations: list[str] = field(default_factory=list)
    prompt: str = ""
    model: str = ""


class DocRetriever:
    def __init__(
        self,
        embedder: Embedder,
        index: ChunkIndex,
        source: str,
        chunker: Chunker = "heading",
        k: int = 5,
    ) -> None:
        self._embedder = embedder
        self._index = index
        self._source = source
        self._chunker: Chunker = chunker
        self._k = k

    def retrieve(self, title: str, body: str) -> list[DocChunk]:
        vector = self._embedder.embed([issue_query(title, body)])[0]
        return [c for c, _ in self._index.search(self._source, self._chunker, vector, self._k)]


def format_context(chunks: Sequence[DocChunk], max_chars: int = 1200) -> str:
    return "\n\n".join(f"[{chunk_id(c)}] {c.text[:max_chars]}" for c in chunks)


class AnswerDrafter:
    def __init__(
        self,
        llm: LLMClient,
        retriever: DocRetriever,
        prompt: Prompt | None = None,
        *,
        min_confidence: float = 0.5,
    ) -> None:
        self._llm = llm
        self._retriever = retriever
        self._prompt = prompt or load_prompt("answer", "v1")
        self._min_confidence = min_confidence

    def draft(self, title: str, body: str) -> DraftResult:
        guarded = guard_input(title, body)
        retrieved = self._retriever.retrieve(title, body)
        messages = self._prompt.render(
            context=format_context(retrieved), title=guarded.title, body=guarded.fenced_body
        )
        draft, completion = self._llm.structured(
            ANSWER_STEP, messages, AnswerDraft, prompt=self._prompt.ref
        )
        clean = guard_output(draft).value
        by_id = {chunk_id(c): c for c in retrieved}
        valid = [by_id[c] for c in dict.fromkeys(clean.citations) if c in by_id]
        invalid = [c for c in clean.citations if c not in by_id]
        # A reply that cites nothing it was given is not grounded: a human must look.
        needs_human = (
            guarded.flagged or not valid or bool(invalid) or clean.confidence < self._min_confidence
        )
        return DraftResult(
            reply=clean.reply,
            citations=valid,
            confidence=clean.confidence,
            needs_human=needs_human,
            retrieved=retrieved,
            invalid_citations=invalid,
            prompt=self._prompt.ref,
            model=completion.model,
        )
