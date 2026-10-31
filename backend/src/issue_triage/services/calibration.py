"""Collect routing signals by running the real agent, and the expected action per issue."""

import random
from collections import defaultdict
from collections.abc import Callable, Sequence

from issue_triage.domain.issues import Issue
from issue_triage.domain.replay import maintainer_outcome
from issue_triage.services.agent.runner import IssueInput, TriageAgent
from issue_triage.services.routing import RoutingSignals

ACTION_ORDER = ("duplicate", "needs_info", "answer", "summary")


def expected_action(issue: Issue) -> str | None:
    """What maintainers did, as an agent action; typed issues with nothing else → summary.

    Untyped issues with no recorded action are left out: there is no ground truth.
    """
    outcome = maintainer_outcome(issue)
    if outcome.expected_action is not None:
        return outcome.expected_action
    if outcome.type_label in ("bug", "enhancement", "other"):
        return "summary"
    return None


def stratified(issues: Sequence[Issue], per_class: int, seed: int = 7) -> list[tuple[Issue, str]]:
    """Up to ``per_class`` issues per expected action, reproducibly."""
    pools: dict[str, list[Issue]] = defaultdict(list)
    for issue in issues:
        action = expected_action(issue)
        if action is not None:
            pools[action].append(issue)
    rng = random.Random(seed)
    chosen: list[tuple[Issue, str]] = []
    for action in ACTION_ORDER:
        pool = sorted(pools.get(action, []), key=lambda i: i.number)
        for issue in rng.sample(pool, min(per_class, len(pool))):
            chosen.append((issue, action))
    return chosen


def collect_signals(
    issues: Sequence[Issue],
    agent: TriageAgent,
    needs_info: Callable[[str, str], float] | None,
    missing_info: Callable[[str], bool],
    on_progress: Callable[[int], None] | None = None,
) -> list[RoutingSignals]:
    signals = []
    for index, issue in enumerate(issues, start=1):
        status = agent.start(
            IssueInput(issue.repository, issue.number, issue.title, issue.body, issue.created_at)
        )
        similar = status.retrieval.similar if status.retrieval else []
        best = max(similar, key=lambda s: s.similarity, default=None)
        signals.append(
            RoutingSignals(
                triage_label=status.triage.label if status.triage else "other",
                best_similarity=best.similarity if best else None,
                best_number=best.number if best else None,
                needs_info_probability=needs_info(issue.title, issue.body) if needs_info else None,
                missing_info=missing_info(issue.body),
            )
        )
        if on_progress and index % 20 == 0:
            on_progress(index)
    return signals
