"""What maintainers actually did with an issue, to show beside a replayed suggestion.

This is information from after ``as_of``: it is displayed and compared, never given to the
agent (D-004).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from issue_triage.domain.evaluation import (
    NEEDS_INFO_LABELS,
    duplicate_target,
    first_maintainer_reply,
    type_class,
)
from issue_triage.domain.issues import Issue


@dataclass(frozen=True)
class MaintainerOutcome:
    state: str
    state_reason: str | None
    closed_at: datetime | None
    labels: tuple[str, ...]
    type_label: str | None
    duplicate_of: int | None
    needs_info: bool
    first_reply: str | None

    @property
    def expected_action(self) -> str | None:
        """The agent action that would have matched what maintainers did, if any."""
        if self.duplicate_of is not None:
            return "duplicate"
        if self.needs_info:
            return "needs_info"
        if self.type_label == "question" and self.first_reply:
            return "answer"
        return None


def maintainer_outcome(issue: Issue) -> MaintainerOutcome:
    target = duplicate_target(issue)
    return MaintainerOutcome(
        state=issue.state,
        state_reason=issue.state_reason,
        closed_at=issue.closed_at,
        labels=issue.labels,
        type_label=type_class(issue),
        duplicate_of=target[1] if target and target[0] == issue.repository else None,
        needs_info=bool(NEEDS_INFO_LABELS.intersection(issue.labels)),
        first_reply=first_maintainer_reply(issue),
    )


def agreement(
    outcome: MaintainerOutcome, action: str, citations: tuple[str, ...], triage_label: str | None
) -> dict[str, Any]:
    """Per-aspect agreement; None where the maintainers' history says nothing."""
    expected = outcome.expected_action
    duplicate_match = None
    if outcome.duplicate_of is not None:
        duplicate_match = action == "duplicate" and f"#{outcome.duplicate_of}" in citations
    return {
        "expected_action": expected,
        "action_matches": None if expected is None else action == expected,
        "duplicate_target_matches": duplicate_match,
        "type_matches": None
        if outcome.type_label is None or triage_label is None
        else triage_label == outcome.type_label,
    }
