"""Live mode: sandbox intake, signed webhooks and allowlisted posting (D-014)."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from issue_triage.domain.pseudonymize import Pseudonymizer
from issue_triage.domain.publishing import (
    PostingRefused,
    SafePublisher,
    check_allowed,
    valid_signature,
)
from issue_triage.infrastructure.db.issues import PostgresIssueRepository
from issue_triage.infrastructure.db.runs import PostgresRunStore
from issue_triage.infrastructure.github_rest import issue_from_payload
from issue_triage.main import create_app
from issue_triage.services.agent.graph import build_graph
from issue_triage.services.agent.runner import TriageAgent
from issue_triage.services.live import LiveIntake
from issue_triage.services.review_queue import ReviewQueue

from .helpers import IsolatedSettings
from .test_agent import deps

SANDBOX = "maideps/triage-sandbox"
TARGET = "astral-sh/uv"
SECRET = "s3cret-webhook"


def payload(number: int = 1, repository: str = SANDBOX, login: str = "alice") -> dict[str, Any]:
    return {
        "action": "opened",
        "repository": {"full_name": repository},
        "issue": {
            "number": number,
            "title": "How do I move the cache folder?",
            "body": f"cc @{login}: How do I move the cache folder somewhere else? " * 3,
            "user": {"login": login},
            "author_association": "OWNER",
            "state": "open",
            "created_at": "2026-10-30T10:00:00Z",
            "closed_at": None,
            "labels": [{"name": "question"}],
            "comments": 0,
        },
    }


class RecordingPoster:
    def __init__(self) -> None:
        self.posts: list[tuple[str, int, str]] = []

    def post_comment(self, repository: str, number: int, body: str) -> str:
        self.posts.append((repository, number, body))
        return f"https://github.com/{repository}/issues/{number}#issuecomment-1"


def test_allowlist_and_protected_target() -> None:
    check_allowed(SANDBOX, [SANDBOX], [TARGET])
    check_allowed(SANDBOX.upper(), [SANDBOX], [TARGET])
    with pytest.raises(PostingRefused, match="allowlist"):
        check_allowed("someone/else", [SANDBOX], [TARGET])
    with pytest.raises(PostingRefused, match="protected"):
        check_allowed(TARGET, [SANDBOX, TARGET], [TARGET])  # listed by mistake: still refused


def test_safe_publisher_refuses_before_calling_github() -> None:
    poster = RecordingPoster()
    publisher = SafePublisher(poster, [SANDBOX], [TARGET])

    with pytest.raises(PostingRefused):
        publisher.post("someone/else", 1, "hello")
    with pytest.raises(PostingRefused):
        publisher.post(TARGET, 1, "hello")

    assert poster.posts == []
    assert publisher.post(SANDBOX, 1, "hello").endswith("#issuecomment-1")


def test_webhook_signatures() -> None:
    body = b'{"action":"opened"}'
    good = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

    assert valid_signature(SECRET, body, good)
    assert not valid_signature(SECRET, body + b" ", good)
    assert not valid_signature(SECRET, body, None)
    assert not valid_signature("", body, good)


def test_payload_parsing() -> None:
    issue = issue_from_payload(SANDBOX, payload(7)["issue"])

    assert (issue.number, issue.state, issue.labels) == (7, "OPEN", ("question",))
    assert issue.created_at == datetime(2026, 10, 30, 10, tzinfo=UTC)


@pytest.fixture
def services(
    sessions: sessionmaker[Session],
) -> tuple[ReviewQueue, LiveIntake, RecordingPoster, PostgresIssueRepository]:
    issues = PostgresIssueRepository(sessions)
    poster = RecordingPoster()
    queue = ReviewQueue(
        TriageAgent(build_graph(deps("question"), InMemorySaver())),
        PostgresRunStore(sessions),
        issues,
        SafePublisher(poster, [SANDBOX], [TARGET]),
    )
    live = LiveIntake(queue, issues, Pseudonymizer("salt"), [SANDBOX], issue_from_payload)
    return queue, live, poster, issues


def test_webhook_queues_pseudonymized_sandbox_issues_only(
    services: tuple[ReviewQueue, LiveIntake, RecordingPoster, PostgresIssueRepository],
) -> None:
    queue, live, _, issues = services

    assert live.handle_webhook("ping", {}) == "pong"
    assert live.handle_webhook("issues", {**payload(), "action": "edited"}) == "ignored"
    assert live.handle_webhook("issues", payload(repository="someone/else")).startswith("ignored")
    assert live.handle_webhook("issues", payload(3)) == f"queued {SANDBOX}#3"

    stored = issues.get(SANDBOX, 3)
    assert stored is not None and "alice" not in stored.author and "@alice" not in stored.body
    assert [r.number for r in queue.list("pending")] == [3]


def test_approved_reply_is_posted_to_the_sandbox(
    services: tuple[ReviewQueue, LiveIntake, RecordingPoster, PostgresIssueRepository],
) -> None:
    queue, live, poster, _ = services
    live.handle_webhook("issues", payload(4))

    detail = queue.review(SANDBOX, 4, "edit", "Set UV_CACHE_DIR.", "maintainer")

    assert poster.posts == [(SANDBOX, 4, "Set UV_CACHE_DIR.")]
    assert detail.run.evidence["posted_url"].endswith("#issuecomment-1")


def test_rejections_and_non_allowlisted_repositories_never_post(
    services: tuple[ReviewQueue, LiveIntake, RecordingPoster, PostgresIssueRepository],
) -> None:
    queue, live, poster, issues = services
    live.handle_webhook("issues", payload(5))
    queue.review(SANDBOX, 5, "reject")

    target_issue = issue_from_payload(TARGET, payload(6)["issue"])
    issues.upsert([target_issue])
    queue.enqueue(TARGET, 6)
    approved = queue.review(TARGET, 6, "approve")

    assert poster.posts == []
    assert approved.run.status == "approved" and "posted_url" not in approved.run.evidence


class ListSource:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items

    def open_issues(self, repository: str) -> list[Any]:
        return [issue_from_payload(repository, i) for i in self.items]


def test_polling_queues_only_new_sandbox_issues(
    services: tuple[ReviewQueue, LiveIntake, RecordingPoster, PostgresIssueRepository],
) -> None:
    _, live, _, _ = services
    source = ListSource([payload(8)["issue"], payload(9)["issue"]])

    first = live.poll(source, SANDBOX)
    second = live.poll(source, SANDBOX)

    assert [d.run.number for d in first] == [8, 9]
    assert second == []
    assert live.poll(source, "someone/else") == []


def test_webhook_endpoint_checks_the_signature(
    services: tuple[ReviewQueue, LiveIntake, RecordingPoster, PostgresIssueRepository],
) -> None:
    queue, live, _, _ = services
    settings = IsolatedSettings(environment="test", webhook_secret=SecretStr(SECRET))
    client = TestClient(create_app(settings, lambda: queue, lambda: live))
    body = json.dumps(payload(11)).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    headers = {"X-GitHub-Event": "issues", "Content-Type": "application/json"}

    forged = client.post(
        "/api/webhooks/github",
        content=body,
        headers={**headers, "X-Hub-Signature-256": "sha256=00"},
    )
    accepted = client.post(
        "/api/webhooks/github", content=body, headers={**headers, "X-Hub-Signature-256": signature}
    )

    assert forged.status_code == 401
    assert accepted.json() == {"result": f"queued {SANDBOX}#11"}
    unconfigured = TestClient(
        create_app(IsolatedSettings(environment="test"), lambda: queue, lambda: live)
    )
    assert unconfigured.post("/api/webhooks/github", content=body).status_code == 503
