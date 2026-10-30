"""Use case: the maintainer's review queue on top of the triage agent (feat-014).

Every proposal the agent produces becomes a queue record with its evidence; every review
resumes the agent and is kept as feedback (original text, final text, who decided).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from issue_triage.domain.issues import Issue
from issue_triage.domain.replay import MaintainerOutcome, agreement, maintainer_outcome
from issue_triage.domain.reviews import (
    FeedbackRecord,
    ReviewDecision,
    RunRecord,
    RunState,
    RunStore,
)
from issue_triage.services.agent.runner import IssueInput, RunStatus
from issue_triage.services.agent.state import Review


class Agent(Protocol):
    def start(self, issue: IssueInput) -> RunStatus: ...

    def resume(self, repository: str, number: int) -> RunStatus: ...

    def review(self, repository: str, number: int, review: Review) -> RunStatus: ...

    def status(self, repository: str, number: int) -> RunStatus: ...


class IssueLookup(Protocol):
    def get(self, repository: str, number: int) -> Issue | None: ...


class NotFound(LookupError):
    pass


class NotPending(RuntimeError):
    pass


@dataclass(frozen=True)
class RunDetail:
    run: RunRecord
    issue_title: str
    issue_body: str
    issue_created_at: datetime
    issue_labels: tuple[str, ...]
    maintainer: MaintainerOutcome
    agreement: dict[str, Any]
    feedback: Sequence[FeedbackRecord]


class ReviewQueue:
    def __init__(self, agent: Agent, runs: RunStore, issues: IssueLookup) -> None:
        self._agent = agent
        self._runs = runs
        self._issues = issues

    def _issue(self, repository: str, number: int) -> Issue:
        issue = self._issues.get(repository, number)
        if issue is None:
            raise NotFound(f"{repository}#{number} is not loaded")
        return issue

    def _evidence(self, repository: str, status: RunStatus) -> dict[str, Any]:
        similar = []
        for s in status.retrieval.similar if status.retrieval else []:
            earlier = self._issues.get(repository, s.number)
            similar.append(
                {
                    "number": s.number,
                    "similarity": s.similarity,
                    "title": earlier.title if earlier else "",
                }
            )
        return {
            "decision": status.decision.model_dump() if status.decision else None,
            "triage": status.triage.model_dump() if status.triage else None,
            "guard": status.guard.model_dump() if status.guard else None,
            "similar": similar,
        }

    def _record(self, issue: Issue, status: RunStatus) -> RunRecord:
        if status.proposal is None:
            raise RuntimeError(f"{status.thread_id} has no proposal")
        run_state: RunState = "pending"
        final_text = None
        if status.outcome is not None:
            run_state = status.outcome.status
            final_text = status.outcome.final_text
        return RunRecord(
            repository=issue.repository,
            number=issue.number,
            title=issue.title,
            status=run_state,
            action=status.proposal.action,
            confidence=status.proposal.confidence,
            needs_attention=status.proposal.needs_attention,
            proposal_text=status.proposal.text,
            citations=tuple(status.proposal.citations),
            evidence=self._evidence(issue.repository, status),
            final_text=final_text,
        )

    def enqueue(self, repository: str, number: int) -> RunDetail:
        """Run the agent for an issue as of its creation time (or pick up its existing run)."""
        issue = self._issue(repository, number)
        status = self._agent.status(repository, number)
        if not status.next_nodes and status.outcome is None:
            status = self._agent.start(
                IssueInput(repository, number, issue.title, issue.body, issue.created_at)
            )
        elif status.next_nodes and not status.awaiting_review:
            status = self._agent.resume(repository, number)
        self._runs.upsert(self._record(issue, status))
        return self.detail(repository, number)

    def list(self, status: RunState | None = None, limit: int = 100) -> list[RunRecord]:
        return self._runs.list(status, limit)

    def detail(self, repository: str, number: int) -> RunDetail:
        run = self._runs.get(repository, number)
        if run is None:
            raise NotFound(f"no triage run for {repository}#{number}")
        issue = self._issue(repository, number)
        outcome = maintainer_outcome(issue)  # display only: never fed to the agent
        return RunDetail(
            run=run,
            issue_title=issue.title,
            issue_body=issue.body,
            issue_created_at=issue.created_at,
            issue_labels=issue.labels,
            maintainer=outcome,
            agreement=agreement(
                outcome,
                run.action,
                run.citations,
                (run.evidence.get("triage") or {}).get("label"),
            ),
            feedback=self._runs.feedback(repository, number),
        )

    def review(
        self,
        repository: str,
        number: int,
        decision: ReviewDecision,
        text: str | None = None,
        reviewer: str = "maintainer",
    ) -> RunDetail:
        run = self._runs.get(repository, number)
        if run is None:
            raise NotFound(f"no triage run for {repository}#{number}")
        if run.status != "pending":
            raise NotPending(f"{repository}#{number} was already {run.status}")
        review = Review(decision=decision, text=text, reviewer=reviewer)
        status = self._agent.review(repository, number, review)
        self._runs.upsert(self._record(self._issue(repository, number), status))
        self._runs.add_feedback(
            FeedbackRecord(
                repository,
                number,
                decision,
                run.proposal_text,
                status.outcome.final_text if status.outcome else None,
                reviewer,
            )
        )
        return self.detail(repository, number)
