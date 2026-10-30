"""Use case: reformulate an issue into focused search queries (an LLM step)."""

from pydantic import BaseModel, Field

from issue_triage.services.guardrails import guard_input, guard_output
from issue_triage.services.llm import LLMClient, StructuredOutputError
from issue_triage.services.prompts import Prompt, load_prompt

QUERY_REWRITE_STEP = "query_rewrite"


class QueryRewrite(BaseModel):
    queries: list[str] = Field(default_factory=list, max_length=3)


class QueryRewriter:
    def __init__(self, llm: LLMClient, prompt: Prompt | None = None) -> None:
        self._llm = llm
        self._prompt = prompt or load_prompt("query_rewrite", "v1")

    def rewrite(self, title: str, body: str) -> list[str]:
        """Up to three non-empty queries; an unusable model reply yields none."""
        guarded = guard_input(title, body)
        messages = self._prompt.render(title=guarded.title, body=guarded.fenced_body)
        try:
            result, _ = self._llm.structured(
                QUERY_REWRITE_STEP, messages, QueryRewrite, prompt=self._prompt.ref
            )
        except StructuredOutputError:
            return []
        queries = guard_output(result).value.queries
        return [q.strip() for q in queries if q.strip()][:3]
