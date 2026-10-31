"""Calibrated routing (feat-021): the policy, the needs-info model and the search."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from issue_triage.domain.issues import Comment, DuplicateLink, Issue
from issue_triage.infrastructure.embeddings import HashingEmbedder
from issue_triage.services.agent.graph import build_graph
from issue_triage.services.agent.runner import IssueInput, TriageAgent
from issue_triage.services.calibration import expected_action, stratified
from issue_triage.services.routing import (
    LEGACY,
    NEVER,
    NeedsInfoModel,
    RoutingPolicy,
    RoutingSignals,
    action_report,
    calibrate,
    policy_grid,
    route,
)

from .test_agent import deps

T0 = datetime(2025, 1, 1, tzinfo=UTC)


def signals(
    label: str = "bug",
    similarity: float | None = None,
    probability: float | None = None,
    missing: bool = False,
) -> RoutingSignals:
    return RoutingSignals(
        label, similarity, 7 if similarity is not None else None, probability, missing
    )


def test_legacy_policy_keeps_the_original_rules() -> None:
    assert route(signals(similarity=0.85), LEGACY)[0] == "duplicate"
    assert route(signals(similarity=0.79), LEGACY)[0] == "summary"
    assert route(signals("question"), LEGACY)[0] == "answer"
    assert route(signals("bug", missing=True), LEGACY)[0] == "needs_info"
    assert route(signals("enhancement", missing=True), LEGACY)[0] == "summary"
    assert route(signals("bug", probability=0.99), LEGACY)[0] == "summary"  # model ignored


def test_needs_info_threshold_labels_and_precedence() -> None:
    policy = RoutingPolicy(0.9, 0.6, ("bug", "question"), needs_info_first=False)

    assert route(signals("bug", probability=0.7), policy)[0] == "needs_info"
    assert route(signals("bug", probability=0.5), policy)[0] == "summary"
    assert route(signals("question", probability=0.7), policy)[0] == "answer"
    assert (
        route(signals("question", probability=0.7), replace(policy, needs_info_first=True))[0]
        == "needs_info"
    )
    assert route(signals("bug", similarity=0.95, probability=0.9), policy)[0] == "duplicate"
    assert (
        route(signals("bug", similarity=0.95), replace(policy, duplicate_threshold=NEVER))[0]
        == "summary"
    )


def test_policy_round_trips_through_json() -> None:
    policy = RoutingPolicy(0.7, 0.4, ("bug", "other"), True)

    assert RoutingPolicy.from_json(policy.to_json()) == policy


def test_needs_info_model_ranks_positives_higher() -> None:
    embedder = HashingEmbedder(dimension=64)
    vague = ["it does not work", "broken please fix", "error happens sometimes", "crash help"] * 10
    detailed = [
        "uv 0.9.1 linux: `uv sync` fails with exit 2, pyproject attached, steps to reproduce",
        "reproduction: uv init demo && uv add torch==2.5.1 on macOS 15, full log below",
    ] * 20
    model = NeedsInfoModel().fit(
        embedder.embed(vague + detailed), [True] * len(vague) + [False] * len(detailed)
    )

    vague_p, detailed_p = model.probability(embedder.embed(["it is broken, help", detailed[0]]))

    assert vague_p > 0.5 > detailed_p


def test_calibration_picks_the_policy_that_matches_maintainers() -> None:
    cases = [
        (signals("bug", similarity=0.72), "duplicate"),
        (signals("bug", similarity=0.74), "duplicate"),
        (signals("bug", similarity=0.5, probability=0.8), "needs_info"),
        (signals("bug", similarity=0.4, probability=0.7), "needs_info"),
        (signals("question", similarity=0.3, probability=0.2), "answer"),
        (signals("bug", similarity=0.5, probability=0.2), "summary"),
        (signals("enhancement", similarity=0.6, probability=0.1), "summary"),
    ]

    best, rows = calibrate([s for s, _ in cases], [e for _, e in cases], policy_grid())

    assert rows[0]["macro_f1"] == 1.0
    assert 0.6 <= best.duplicate_threshold <= 0.7
    assert best.needs_info_threshold is not None and 0.3 <= best.needs_info_threshold <= 0.7
    legacy = action_report([e for _, e in cases], [route(s, LEGACY)[0] for s, _ in cases])
    assert legacy["macro_f1"] < 1.0


def issue(number: int, **overrides: object) -> Issue:
    base = Issue(
        "acme/widgets",
        number,
        "t",
        "b",
        "user-a",
        "NONE",
        "CLOSED",
        "COMPLETED",
        T0 + timedelta(days=number),
        None,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_expected_actions_and_stratified_sampling() -> None:
    reply = (Comment("user-m", "MEMBER", "Use --python.", T0),)
    issues = [
        issue(
            1,
            labels=("bug",),
            state_reason="DUPLICATE",
            duplicate_of=DuplicateLink("acme/widgets", 9),
        ),
        issue(2, labels=("bug", "needs-mre")),
        issue(3, labels=("question",), comments=reply),
        issue(4, labels=("enhancement",)),
        issue(5, labels=()),  # untyped, nothing recorded: no ground truth
        issue(6, labels=("bug",)),
    ]

    assert [expected_action(i) for i in issues] == [
        "duplicate",
        "needs_info",
        "answer",
        "summary",
        None,
        "summary",
    ]
    sample = stratified(issues, per_class=1)
    assert sorted(a for _, a in sample) == ["answer", "duplicate", "needs_info", "summary"]
    assert stratified(issues, per_class=1) == sample


@pytest.mark.parametrize(("probability", "action"), [(0.9, "needs_info"), (0.1, "summary")])
def test_agent_decide_uses_the_calibrated_policy(probability: float, action: str) -> None:
    d = deps("bug")
    d.routing = RoutingPolicy(0.95, 0.5, ("bug",), False)
    d.needs_info = lambda title, body: probability
    agent = TriageAgent(build_graph(d, InMemorySaver()))
    body = "uv 0.9.1 on Linux: `uv sync` fails with exit code 2 after adding a git source " * 3

    status = agent.start(IssueInput("acme/widgets", 30, "Sync fails", body, T0))

    assert status.decision is not None and status.decision.action == action
