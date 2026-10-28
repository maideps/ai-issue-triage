from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from issue_triage.domain.issues import Comment, DuplicateLink, Issue
from issue_triage.infrastructure.db.issues import PostgresIssueRepository
from issue_triage.infrastructure.db.models import Base
from issue_triage.infrastructure.jsonl_store import JsonlIssueStore
from issue_triage.services.loading import load_issues

REPO = "acme/widgets"


def issue(number: int, *, day: int = 1, comments: int = 1) -> Issue:
    return Issue(
        repository=REPO,
        number=number,
        title=f"Issue {number}",
        body="body",
        author="user-1",
        author_association="NONE",
        state="CLOSED",
        state_reason="DUPLICATE" if number == 2 else "COMPLETED",
        created_at=datetime(2025, 1, day, tzinfo=UTC),
        closed_at=datetime(2025, 2, day, tzinfo=UTC),
        labels=("bug", "area:windows"),
        duplicate_of=DuplicateLink(REPO, 1) if number == 2 else None,
        comments=tuple(
            Comment("user-2", "MEMBER", f"reply {i}", datetime(2025, 1, day, 12, i, tzinfo=UTC))
            for i in range(comments)
        ),
        comment_count=comments,
    )


@pytest.fixture
def repository(sessions: sessionmaker[Session]) -> PostgresIssueRepository:
    return PostgresIssueRepository(sessions)


def test_migrations_match_the_orm_models(database_url: str) -> None:
    engine = create_engine(database_url)
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    engine.dispose()

    assert diff == []


def test_round_trips_every_field(repository: PostgresIssueRepository) -> None:
    original = issue(2, comments=3)

    repository.upsert([original])

    assert repository.get(REPO, 2) == original
    assert repository.get(REPO, 99) is None


def test_upsert_replaces_fields_and_comments(repository: PostgresIssueRepository) -> None:
    repository.upsert([issue(1, comments=3)])

    updated = replace(issue(1, comments=1), state="OPEN", labels=("question",))
    repository.upsert([updated])

    assert repository.get(REPO, 1) == updated
    assert repository.count(REPO) == 1


def test_list_is_ordered_and_filters_by_creation_time(
    repository: PostgresIssueRepository,
) -> None:
    repository.upsert([issue(3, day=3), issue(1, day=1), issue(2, day=2)])

    assert [i.number for i in repository.list(REPO)] == [1, 2, 3]
    before = repository.list(REPO, created_before=datetime(2025, 1, 3, tzinfo=UTC))
    assert [i.number for i in before] == [1, 2]


def test_repositories_are_kept_apart(repository: PostgresIssueRepository) -> None:
    repository.upsert([issue(1), replace(issue(1), repository="other/repo")])

    assert repository.count(REPO) == 1
    assert repository.count("other/repo") == 1


def test_loader_is_idempotent(repository: PostgresIssueRepository, tmp_path: Path) -> None:
    store = JsonlIssueStore(tmp_path)
    store.append(REPO, [issue(n, day=n) for n in range(1, 8)])

    first = load_issues(store, repository, REPO, batch_size=3)
    second = load_issues(store, repository, REPO, batch_size=3)

    assert first == second == 7
    assert repository.count(REPO) == 7
    assert repository.list(REPO) == store.read_all(REPO)
