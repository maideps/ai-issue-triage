"""Evaluate drafted answers against the maintainers' real replies (feat-011, D-010)."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from statistics import mean
from typing import Any

from pydantic import BaseModel, Field

from issue_triage.domain.answer_metrics import cosine, lexical_faithfulness
from issue_triage.domain.docs import Embedder
from issue_triage.domain.evaluation import AnswerCase, docs_link_pages
from issue_triage.services.answers import AnswerDrafter
from issue_triage.services.docs_index import issue_query
from issue_triage.services.llm import CallLedger, LLMClient, StructuredOutputError
from issue_triage.services.prompts import Prompt, load_prompt

JUDGE_STEP = "judge"
TEXT_CHARS = 3000


class JudgeVerdict(BaseModel):
    score: int = Field(ge=1, le=5)
    reason: str = ""


class Judge:
    def __init__(self, llm: LLMClient, prompt: Prompt | None = None) -> None:
        self._llm = llm
        self._prompt = prompt or load_prompt("judge", "v1")

    def grade(self, question: str, reference: str, draft: str) -> int | None:
        messages = self._prompt.render(
            question=question[:TEXT_CHARS],
            reference=reference[:TEXT_CHARS],
            draft=draft[:TEXT_CHARS],
        )
        try:
            verdict, _ = self._llm.structured(
                JUDGE_STEP, messages, JudgeVerdict, prompt=self._prompt.ref
            )
        except StructuredOutputError:
            return None
        return verdict.score


def _avg(values: Sequence[float]) -> float:
    return round(mean(values), 4) if values else 0.0


def evaluate_answers(
    cases: Sequence[AnswerCase],
    drafter: AnswerDrafter,
    judge: Judge,
    embedder: Embedder,
    ledger: CallLedger,
    *,
    docs_base_url: str,
    on_progress: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    faithfulness: list[float] = []
    relevancy: list[float] = []
    reference_similarity: list[float] = []
    judge_scores: list[int] = []
    page_hits: list[float] = []
    valid = invalid = needs_human = failures = 0
    models: set[str] = set()
    for index, case in enumerate(cases, start=1):
        try:
            result = drafter.draft(case.title, case.body)
        except StructuredOutputError:
            failures += 1
            continue
        models.add(result.model)
        valid += len(result.citations)
        invalid += len(result.invalid_citations)
        needs_human += result.needs_human
        faithfulness.append(lexical_faithfulness(result.reply, [c.text for c in result.citations]))
        question = issue_query(case.title, case.body)
        q_vec, reply_vec, ref_vec = embedder.embed(
            [question, result.reply, case.reference_answer[:TEXT_CHARS]]
        )
        relevancy.append(cosine(q_vec, reply_vec))
        reference_similarity.append(cosine(reply_vec, ref_vec))
        linked = docs_link_pages(case.reference_answer, docs_base_url)
        if linked:
            page_hits.append(float(any(c.page in linked for c in result.citations)))
        score = judge.grade(question, case.reference_answer, result.reply)
        if score is not None:
            judge_scores.append(score)
        if on_progress and index % 50 == 0:
            on_progress(index)

    drafted = len(cases) - failures
    return {
        "task": "answers",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "models": sorted(models),
        "n": len(cases),
        "drafted": drafted,
        "failures": failures,
        "faithfulness_lexical": _avg(faithfulness),
        "answer_relevancy_cosine": _avg(relevancy),
        "reference_similarity_cosine": _avg(reference_similarity),
        "judge_score_mean": _avg([float(s) for s in judge_scores]),
        "judge_scored": len(judge_scores),
        "cited_page_matches_maintainer_link": _avg(page_hits),
        "cases_with_maintainer_link": len(page_hits),
        "citation_validity": round(valid / (valid + invalid), 4) if valid + invalid else 0.0,
        "needs_human_rate": round(needs_human / drafted, 4) if drafted else 0.0,
        "llm": ledger.summary(),
    }
