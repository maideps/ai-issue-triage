"""Use case: classify an issue's type with an LLM, behind the guardrails."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from issue_triage.services.guardrails import guard_input, guard_output
from issue_triage.services.llm import LLMClient
from issue_triage.services.prompts import Prompt, load_prompt

TRIAGE_STEP = "triage"


class TriageResult(BaseModel):
    label: Literal["bug", "question", "enhancement", "other"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


@dataclass(frozen=True)
class TriageDecision:
    label: str
    confidence: float
    rationale: str
    needs_human: bool
    prompt: str
    model: str


class Triager:
    def __init__(self, llm: LLMClient, prompt: Prompt | None = None) -> None:
        self._llm = llm
        self._prompt = prompt or load_prompt("triage", "v1")

    @property
    def prompt_ref(self) -> str:
        return self._prompt.ref

    def classify(self, title: str, body: str) -> TriageDecision:
        guarded = guard_input(title, body)
        messages = self._prompt.render(title=guarded.title, body=guarded.fenced_body)
        result, completion = self._llm.structured(
            TRIAGE_STEP, messages, TriageResult, prompt=self._prompt.ref
        )
        clean = guard_output(result).value
        return TriageDecision(
            label=clean.label,
            confidence=clean.confidence,
            rationale=clean.rationale,
            needs_human=guarded.flagged,
            prompt=self._prompt.ref,
            model=completion.model,
        )
