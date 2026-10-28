import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from issue_triage.cli import main
from issue_triage.domain.issues import Comment, Issue
from issue_triage.domain.pseudonymize import Pseudonymizer
from issue_triage.domain.sources import IssuePage
from issue_triage.infrastructure.jsonl_store import JsonlIssueStore
from issue_triage.services.ingestion import ingest_issues

from .helpers import IsolatedSettings

REPO = "acme/widgets"


def issue(number: int, author: str = "alice") -> Issue:
    return Issue(
        repository=REPO,
        number=number,
        title=f"Issue {number}",
        body=f"reported by @{author}",
        author=author,
        author_association="NONE",
        state="CLOSED",
        state_reason="COMPLETED",
        created_at=datetime(2025, 1, number, tzinfo=UTC),
        closed_at=None,
        labels=("bug",),
        comments=(
            Comment("maint", "MEMBER", f"thanks @{author}", datetime(2025, 2, 1, tzinfo=UTC)),
        ),
        comment_count=1,
    )


class FakeSource:
    """Three pages of two issues; fails on demand to simulate an interrupted run."""

    def __init__(self, fail_on_cursor: str = "") -> None:
        self.pages = {
            None: IssuePage((issue(1), issue(2, "bob")), "c1", True),
            "c1": IssuePage((issue(3), issue(4)), "c2", True),
            "c2": IssuePage((issue(5, "carol"),), "c3", False),
        }
        self.fail_on_cursor = fail_on_cursor
        self.requested: list[str | None] = []

    def fetch_page(self, repository: str, cursor: str | None) -> IssuePage:
        self.requested.append(cursor)
        if self.fail_on_cursor and cursor == self.fail_on_cursor:
            raise ConnectionError("network down")
        return self.pages[cursor]


@pytest.fixture
def store(tmp_path: Path) -> JsonlIssueStore:
    return JsonlIssueStore(tmp_path)


def test_full_run_stores_every_issue_once(store: JsonlIssueStore) -> None:
    result = ingest_issues(FakeSource(), store, Pseudonymizer("s"), REPO)

    assert result.complete and result.pages == 3 and result.issues == 5
    assert [i.number for i in store.read_all(REPO)] == [1, 2, 3, 4, 5]
    assert store.is_complete(REPO)


def test_interrupted_run_resumes_from_the_saved_cursor(store: JsonlIssueStore) -> None:
    with pytest.raises(ConnectionError):
        ingest_issues(FakeSource(fail_on_cursor="c2"), store, Pseudonymizer("s"), REPO)
    assert store.load_cursor(REPO) == "c2"

    source = FakeSource()
    result = ingest_issues(source, store, Pseudonymizer("s"), REPO)

    assert source.requested == ["c2"]
    assert result.complete
    assert [i.number for i in store.read_all(REPO)] == [1, 2, 3, 4, 5]


def test_max_pages_stops_early_and_a_complete_store_is_not_refetched(
    store: JsonlIssueStore,
) -> None:
    partial = ingest_issues(FakeSource(), store, Pseudonymizer("s"), REPO, max_pages=1)
    assert not partial.complete and partial.issues == 2

    ingest_issues(FakeSource(), store, Pseudonymizer("s"), REPO)
    source = FakeSource()
    again = ingest_issues(source, store, Pseudonymizer("s"), REPO)

    assert again.pages == 0 and source.requested == []


def test_restart_discards_stored_issues(store: JsonlIssueStore) -> None:
    ingest_issues(FakeSource(), store, Pseudonymizer("s"), REPO)

    source = FakeSource()
    ingest_issues(source, store, Pseudonymizer("s"), REPO, restart=True)

    assert source.requested == [None, "c1", "c2"]
    assert len(store.read_all(REPO)) == 5


def test_no_raw_login_reaches_storage(store: JsonlIssueStore, tmp_path: Path) -> None:
    ingest_issues(FakeSource(), store, Pseudonymizer("s"), REPO)

    raw = (tmp_path / "github" / "acme__widgets" / "issues.jsonl").read_text(encoding="utf-8")
    for login in ("alice", "bob", "carol", "maint"):
        assert login not in raw
    assert "MEMBER" in raw  # associations are kept: they mark maintainer answers


def test_store_keeps_the_last_record_for_a_repeated_issue(store: JsonlIssueStore) -> None:
    store.append(REPO, [issue(1)])
    store.append(REPO, [replace(issue(1), state="OPEN")])

    assert [i.state for i in store.read_all(REPO)] == ["OPEN"]


def test_store_round_trips_every_field(store: JsonlIssueStore) -> None:
    original = issue(7)

    store.append(REPO, [original])

    assert store.read_all(REPO) == [original]


def test_cli_ingest_uses_the_configured_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("issue_triage.cli.make_source", lambda settings, page_size: FakeSource())

    code = main(["ingest", REPO], IsolatedSettings(data_dir=tmp_path))

    assert code == 0
    assert "5 stored, complete" in capsys.readouterr().out
    state = json.loads((tmp_path / "github" / "acme__widgets" / "state.json").read_text())
    assert state == {"cursor": "c2", "complete": True}
