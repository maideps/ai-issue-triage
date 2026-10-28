import http.client
import io
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from issue_triage.domain.issues import DuplicateLink
from issue_triage.infrastructure.github_source import (
    GRAPHQL_URL,
    GitHubError,
    GitHubGraphQLSource,
    TransientGitHubError,
    urllib_transport,
)

FIXTURES = Path(__file__).parent / "fixtures" / "github"


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


class RecordedTransport:
    """Replays recorded responses and keeps the variables of every call."""

    def __init__(self, *responses: dict[str, Any] | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(variables)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_source(transport: RecordedTransport, sleeps: list[float]) -> GitHubGraphQLSource:
    return GitHubGraphQLSource(
        transport,
        sleep=sleeps.append,
        now=lambda: datetime(2026, 10, 27, 19, 59, 0, tzinfo=UTC),
    )


def test_parses_issue_fields() -> None:
    page = make_source(RecordedTransport(load("page1.json")), []).fetch_page("acme/widgets", None)

    first, duplicate = page.issues
    assert first.number == 1
    assert first.author == "alice"
    assert first.labels == ("bug", "area:windows")
    assert first.state_reason == "COMPLETED"
    assert first.closed_at == datetime(2025, 1, 5, 9, 0, tzinfo=UTC)
    assert first.comments[0].by_maintainer
    assert first.comments[1].author == ""  # deleted account
    assert duplicate.duplicate_of == DuplicateLink("acme/widgets", 1)
    assert duplicate.comments_truncated


def test_pagination_passes_the_cursor() -> None:
    transport = RecordedTransport(load("page1.json"), load("page2.json"))
    source = make_source(transport, [])

    first = source.fetch_page("acme/widgets", None)
    second = source.fetch_page("acme/widgets", first.next_cursor)

    assert "after" not in transport.calls[0]
    assert transport.calls[1]["after"] == "CURSOR_1"
    assert transport.calls[1]["owner"] == "acme" and transport.calls[1]["name"] == "widgets"
    assert first.has_next_page and not second.has_next_page


def test_waits_for_rate_limit_reset_when_budget_is_low() -> None:
    sleeps: list[float] = []
    transport = RecordedTransport(load("page2.json"), load("page2.json"))
    source = make_source(transport, sleeps)

    source.fetch_page("acme/widgets", None)  # remaining 42 < 100
    assert sleeps == []
    source.fetch_page("acme/widgets", "CURSOR_2")

    assert sleeps == [61.0]  # reset at 20:00:00, now 19:59:00, plus one second


def test_retries_transient_errors_with_backoff() -> None:
    sleeps: list[float] = []
    transport = RecordedTransport(
        TransientGitHubError("HTTP 502"), TransientGitHubError("HTTP 502"), load("page2.json")
    )

    page = make_source(transport, sleeps).fetch_page("acme/widgets", None)

    assert len(page.issues) == 1
    assert sleeps == [1.0, 2.0]


def test_gives_up_after_max_retries_with_capped_backoff() -> None:
    sleeps: list[float] = []
    transport = RecordedTransport(*[TransientGitHubError("down")] * 9)

    with pytest.raises(TransientGitHubError):
        make_source(transport, sleeps).fetch_page("acme/widgets", None)

    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


@pytest.mark.parametrize(
    "error",
    [
        ConnectionResetError(10054, "reset by peer"),
        TimeoutError("timed out"),
        http.client.IncompleteRead(b"partial"),
    ],
)
def test_transport_turns_network_failures_into_transient_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def broken_urlopen(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", broken_urlopen)

    with pytest.raises(TransientGitHubError):
        urllib_transport("token")("query", {})


def test_transport_retries_server_errors_but_not_bad_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def http_error(code: int) -> object:
        def urlopen(*args: object, **kwargs: object) -> None:
            raise urllib.error.HTTPError(GRAPHQL_URL, code, "error", Message(), io.BytesIO(b"{}"))

        return urlopen

    monkeypatch.setattr(urllib.request, "urlopen", http_error(502))
    with pytest.raises(TransientGitHubError):
        urllib_transport("token")("query", {})

    monkeypatch.setattr(urllib.request, "urlopen", http_error(401))
    with pytest.raises(GitHubError):
        urllib_transport("token")("query", {})


def test_graphql_errors_raise() -> None:
    transport = RecordedTransport({"errors": [{"message": "Bad credentials"}]})

    with pytest.raises(GitHubError, match="Bad credentials"):
        make_source(transport, []).fetch_page("acme/widgets", None)


def test_missing_repository_raises() -> None:
    transport = RecordedTransport({"data": {"rateLimit": None, "repository": None}})

    with pytest.raises(GitHubError, match="not found"):
        make_source(transport, []).fetch_page("acme/missing", None)
