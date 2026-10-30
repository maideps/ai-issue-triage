"""Run the triage graph per issue: start it, find what waits for review, resume it."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from issue_triage.services.agent.state import AgentState, Decision, Outcome, Proposal, Review


@dataclass(frozen=True)
class IssueInput:
    repository: str
    number: int
    title: str
    body: str
    as_of: datetime


@dataclass(frozen=True)
class RunStatus:
    thread_id: str
    next_nodes: tuple[str, ...]
    decision: Decision | None
    proposal: Proposal | None
    outcome: Outcome | None

    @property
    def awaiting_review(self) -> bool:
        return self.next_nodes == ("human_gate",)


def thread_id(repository: str, number: int) -> str:
    return f"{repository}#{number}"


class TriageAgent:
    def __init__(self, graph: CompiledStateGraph[AgentState, None, AgentState, AgentState]) -> None:
        self._graph = graph

    @staticmethod
    def _config(repository: str, number: int) -> Any:
        return {"configurable": {"thread_id": thread_id(repository, number)}}

    def start(self, issue: IssueInput) -> RunStatus:
        """Run until the human gate (or the end). Starting again replaces nothing: the
        thread already exists, so callers use ``status`` or ``resume`` for it."""
        config = self._config(issue.repository, issue.number)
        state: AgentState = {
            "repository": issue.repository,
            "number": issue.number,
            "title": issue.title,
            "body": issue.body,
            "as_of": issue.as_of.isoformat(),
        }
        self._graph.invoke(state, config)
        return self.status(issue.repository, issue.number)

    def resume(self, repository: str, number: int) -> RunStatus:
        """Continue a run that stopped on an error, from its last completed node."""
        self._graph.invoke(None, self._config(repository, number))
        return self.status(repository, number)

    def review(self, repository: str, number: int, review: Review) -> RunStatus:
        status = self.status(repository, number)
        if not status.awaiting_review:
            raise RuntimeError(f"{status.thread_id} is not waiting for review")
        self._graph.invoke(Command(resume=review.model_dump()), self._config(repository, number))
        return self.status(repository, number)

    def status(self, repository: str, number: int) -> RunStatus:
        snapshot = self._graph.get_state(self._config(repository, number))
        values: dict[str, Any] = dict(snapshot.values or {})
        return RunStatus(
            thread_id=thread_id(repository, number),
            next_nodes=tuple(snapshot.next),
            decision=Decision.model_validate(values["decision"]) if "decision" in values else None,
            proposal=Proposal.model_validate(values["proposal"]) if "proposal" in values else None,
            outcome=Outcome.model_validate(values["outcome"]) if "outcome" in values else None,
        )
