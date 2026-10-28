"""Use case: copy a tracker's issues into local storage, resumably."""

from collections.abc import Callable
from dataclasses import dataclass, replace

from issue_triage.domain.issues import Comment, Issue
from issue_triage.domain.pseudonymize import Pseudonymizer
from issue_triage.domain.sources import IssueSource, IssueStore


@dataclass(frozen=True)
class IngestionResult:
    repository: str
    pages: int
    issues: int
    complete: bool


def pseudonymize_issue(issue: Issue, pseudonymizer: Pseudonymizer) -> Issue:
    return replace(
        issue,
        title=pseudonymizer.text(issue.title),
        body=pseudonymizer.text(issue.body),
        author=pseudonymizer.user(issue.author),
        comments=tuple(
            Comment(
                author=pseudonymizer.user(comment.author),
                author_association=comment.author_association,
                body=pseudonymizer.text(comment.body),
                created_at=comment.created_at,
            )
            for comment in issue.comments
        ),
    )


def ingest_issues(
    source: IssueSource,
    store: IssueStore,
    pseudonymizer: Pseudonymizer,
    repository: str,
    *,
    max_pages: int | None = None,
    restart: bool = False,
    on_page: Callable[[int, int], None] | None = None,
) -> IngestionResult:
    """Fetch pages from the saved cursor on, pseudonymize them and store them.

    ``on_page`` receives (page number, issues stored so far in this run).
    """
    if restart:
        store.reset(repository)
    if store.is_complete(repository):
        return IngestionResult(repository, pages=0, issues=0, complete=True)

    cursor = store.load_cursor(repository)
    pages = stored = 0
    while max_pages is None or pages < max_pages:
        page = source.fetch_page(repository, cursor)
        store.append(repository, [pseudonymize_issue(i, pseudonymizer) for i in page.issues])
        pages += 1
        stored += len(page.issues)
        complete = not page.has_next_page
        cursor = page.next_cursor if page.has_next_page else cursor
        store.save_cursor(repository, cursor, complete=complete)
        if on_page:
            on_page(pages, stored)
        if complete:
            return IngestionResult(repository, pages, stored, complete=True)
    return IngestionResult(repository, pages, stored, complete=False)
