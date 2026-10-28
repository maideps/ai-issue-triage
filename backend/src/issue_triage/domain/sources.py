"""Interfaces for reading issues from a tracker and keeping them locally."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from issue_triage.domain.issues import Issue


@dataclass(frozen=True)
class IssuePage:
    issues: tuple[Issue, ...]
    next_cursor: str | None
    has_next_page: bool


class IssueSource(Protocol):
    """A tracker that returns issues oldest first, one page at a time."""

    def fetch_page(self, repository: str, cursor: str | None) -> IssuePage: ...


class IssueStore(Protocol):
    """Local storage for ingested issues plus the cursor to resume from.

    Writers append a page, then save the cursor. If a run stops in between, the page is
    appended again on resume, so readers keep the last record for each issue number.
    """

    def load_cursor(self, repository: str) -> str | None: ...

    def is_complete(self, repository: str) -> bool: ...

    def append(self, repository: str, issues: Sequence[Issue]) -> None: ...

    def save_cursor(self, repository: str, cursor: str | None, *, complete: bool) -> None: ...

    def reset(self, repository: str) -> None: ...

    def read_all(self, repository: str) -> list[Issue]: ...


class IssueRepository(Protocol):
    """The queryable home of issues once they are loaded (PostgreSQL in production)."""

    def upsert(self, issues: Sequence[Issue]) -> int: ...

    def get(self, repository: str, number: int) -> Issue | None: ...

    def list(self, repository: str, *, created_before: datetime | None = None) -> list[Issue]: ...

    def count(self, repository: str) -> int: ...
