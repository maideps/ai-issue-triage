"""Use case: load ingested issues from local storage into the database."""

from collections.abc import Callable

from issue_triage.domain.sources import IssueRepository, IssueStore


def load_issues(
    store: IssueStore,
    issues: IssueRepository,
    repository: str,
    *,
    batch_size: int = 500,
    on_batch: Callable[[int], None] | None = None,
) -> int:
    """Upsert every stored issue; running it twice leaves the database unchanged."""
    stored = store.read_all(repository)
    loaded = 0
    for start in range(0, len(stored), batch_size):
        loaded += issues.upsert(stored[start : start + batch_size])
        if on_batch:
            on_batch(loaded)
    return loaded
