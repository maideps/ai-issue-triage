"""The review queue: one record per triaged issue, plus every reviewer decision."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

RunState = Literal["pending", "approved", "rejected"]
ReviewDecision = Literal["approve", "edit", "reject"]


@dataclass(frozen=True)
class RunRecord:
    repository: str
    number: int
    title: str
    status: RunState
    action: str
    confidence: float
    needs_attention: bool
    proposal_text: str
    citations: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    final_text: str | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class FeedbackRecord:
    """What a reviewer did with a proposal: the training signal for later iterations."""

    repository: str
    number: int
    decision: ReviewDecision
    original_text: str
    final_text: str | None
    reviewer: str
    created_at: datetime | None = None


class RunStore(Protocol):
    def upsert(self, run: RunRecord) -> None: ...

    def get(self, repository: str, number: int) -> RunRecord | None: ...

    def list(self, status: RunState | None = None, limit: int = 100) -> list[RunRecord]: ...

    def add_feedback(self, feedback: FeedbackRecord) -> None: ...

    def feedback(self, repository: str, number: int) -> Sequence[FeedbackRecord]: ...

    def decision_counts(self) -> dict[str, int]: ...
