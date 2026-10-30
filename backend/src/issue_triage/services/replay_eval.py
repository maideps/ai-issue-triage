"""Replay a sample of historical issues end to end and compare with the maintainers."""

from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from issue_triage.domain.issues import Issue
from issue_triage.domain.replay import agreement, maintainer_outcome
from issue_triage.services.agent.runner import IssueInput, TriageAgent


def _rate(values: Sequence[bool | None]) -> dict[str, Any]:
    known = [v for v in values if v is not None]
    return {"n": len(known), "rate": round(sum(known) / len(known), 4) if known else None}


def evaluate_replay(
    issues: Sequence[Issue],
    agent: TriageAgent,
    *,
    on_progress: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    actions: Counter[str] = Counter()
    by_expected: dict[str, list[bool]] = {}
    action_matches: list[bool | None] = []
    target_matches: list[bool | None] = []
    type_matches: list[bool | None] = []
    for index, issue in enumerate(issues, start=1):
        status = agent.start(
            IssueInput(issue.repository, issue.number, issue.title, issue.body, issue.created_at)
        )
        if status.proposal is None:
            continue
        outcome = maintainer_outcome(issue)
        verdict = agreement(
            outcome,
            status.proposal.action,
            tuple(status.proposal.citations),
            status.triage.label if status.triage else None,
        )
        actions[status.proposal.action] += 1
        action_matches.append(verdict["action_matches"])
        target_matches.append(verdict["duplicate_target_matches"])
        type_matches.append(verdict["type_matches"])
        if outcome.expected_action is not None:
            by_expected.setdefault(outcome.expected_action, []).append(
                status.proposal.action == outcome.expected_action
            )
        if on_progress and index % 10 == 0:
            on_progress(index)
    return {
        "task": "replay",
        "n": sum(actions.values()),
        "actions": dict(actions),
        "action_agreement": _rate(action_matches),
        "duplicate_target_agreement": _rate(target_matches),
        "type_agreement": _rate(type_matches),
        "action_recall_by_maintainer_action": {
            expected: _rate(list(hits)) for expected, hits in sorted(by_expected.items())
        },
    }
