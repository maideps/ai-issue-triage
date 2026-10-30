"""Replay mode: the suggestion beside what maintainers actually did."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.orm import Session, sessionmaker

from issue_triage.domain.issues import Comment, DuplicateLink, Issue
from issue_triage.domain.replay import agreement, maintainer_outcome
from issue_triage.infrastructure.db.issues import PostgresIssueRepository
from issue_triage.infrastructure.db.runs import PostgresRunStore
from issue_triage.main import create_app
from issue_triage.services.agent.graph import build_graph
from issue_triage.services.agent.runner import TriageAgent
from issue_triage.services.replay_eval import evaluate_replay
from issue_triage.services.review_queue import ReviewQueue

from .helpers import IsolatedSettings
from .test_agent import EARLIER, deps

REPO = "acme/widgets"
T0 = datetime(2025, 1, 1, tzinfo=UTC)


def issue(number: int, **overrides: object) -> Issue:
    base = Issue(
        REPO,
        number,
        f"Issue {number}",
        "body",
        "user-a",
        "NONE",
        "CLOSED",
        "COMPLETED",
        T0 + timedelta(days=number),
        T0 + timedelta(days=number + 2),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_outcome_reads_duplicates_needs_info_and_answers() -> None:
    duplicate = maintainer_outcome(
        issue(5, state_reason="DUPLICATE", duplicate_of=DuplicateLink(REPO, 1), labels=("bug",))
    )
    needs_info = maintainer_outcome(issue(6, labels=("bug", "needs-mre")))
    answered = maintainer_outcome(
        issue(
            7,
            labels=("question",),
            comments=(Comment("user-m", "MEMBER", "Use UV_CACHE_DIR.", T0 + timedelta(days=8)),),
        )
    )
    other = maintainer_outcome(issue(8, labels=("enhancement",)))

    assert (duplicate.duplicate_of, duplicate.expected_action) == (1, "duplicate")
    assert needs_info.expected_action == "needs_info"
    assert (answered.first_reply, answered.expected_action) == ("Use UV_CACHE_DIR.", "answer")
    assert other.expected_action is None and other.type_label == "enhancement"


def test_agreement_compares_action_target_and_type() -> None:
    outcome = maintainer_outcome(
        issue(5, state_reason="DUPLICATE", duplicate_of=DuplicateLink(REPO, 1), labels=("bug",))
    )

    right = agreement(outcome, "duplicate", ("#1",), "bug")
    wrong_target = agreement(outcome, "duplicate", ("#2",), "question")
    no_history = agreement(maintainer_outcome(issue(9)), "summary", (), "bug")

    assert right == {
        "expected_action": "duplicate",
        "action_matches": True,
        "duplicate_target_matches": True,
        "type_matches": True,
    }
    assert wrong_target["action_matches"] and not wrong_target["duplicate_target_matches"]
    assert wrong_target["type_matches"] is False
    assert no_history == {
        "expected_action": None,
        "action_matches": None,
        "duplicate_target_matches": None,
        "type_matches": None,
    }


@pytest.fixture
def client(sessions: sessionmaker[Session]) -> TestClient:
    issues = PostgresIssueRepository(sessions)
    issues.upsert(
        [
            issue(1, title="Resolver panic", body=EARLIER[1], labels=("bug",)),
            issue(
                10,
                title="uv lock panics on windows",
                body=EARLIER[1],
                state_reason="DUPLICATE",
                duplicate_of=DuplicateLink(REPO, 1),
                labels=("bug", "duplicate"),
                comments=(Comment("user-m", "MEMBER", "Duplicate of #1", T0 + timedelta(days=11)),),
            ),
        ]
    )
    queue = ReviewQueue(
        TriageAgent(build_graph(deps("bug", similar=[1]), InMemorySaver())),
        PostgresRunStore(sessions),
        issues,
    )
    return TestClient(create_app(IsolatedSettings(environment="test"), lambda: queue))


def test_replaying_a_recorded_issue_shows_both_outcomes(client: TestClient) -> None:
    detail = client.post(f"/api/runs/{REPO}/10").json()

    assert detail["run"]["action"] == "duplicate"
    assert detail["run"]["citations"] == ["#1"]
    assert detail["maintainer"]["duplicate_of"] == 1
    assert detail["maintainer"]["state_reason"] == "DUPLICATE"
    assert detail["maintainer"]["first_reply"] == "Duplicate of #1"
    assert detail["agreement"]["action_matches"] is True
    assert detail["agreement"]["duplicate_target_matches"] is True
    # The agent saw only the issue as opened: its evidence never mentions the outcome.
    assert "Duplicate of #1" not in str(detail["run"]["evidence"])


def test_replay_evaluation_reports_agreement_by_maintainer_action() -> None:
    issues = [
        issue(1, title="Resolver panic", body=EARLIER[1], labels=("bug",)),
        issue(
            10,
            title="uv lock panics on windows",
            body=EARLIER[1],
            state_reason="DUPLICATE",
            duplicate_of=DuplicateLink(REPO, 1),
            labels=("bug",),
        ),
        issue(11, title="Crash", body="it crashes", labels=("bug", "needs-mre")),
    ]
    agent = TriageAgent(build_graph(deps("bug", similar=[1]), InMemorySaver()))

    summary = evaluate_replay(issues[1:], agent)

    assert summary["n"] == 2
    assert summary["actions"]["duplicate"] >= 1
    assert summary["action_recall_by_maintainer_action"]["duplicate"] == {"n": 1, "rate": 1.0}
    assert summary["duplicate_target_agreement"] == {"n": 1, "rate": 1.0}
