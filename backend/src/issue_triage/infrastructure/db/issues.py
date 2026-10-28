"""PostgreSQL implementation of the issue repository."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from issue_triage.domain.issues import Comment, DuplicateLink, Issue
from issue_triage.infrastructure.db.models import CommentRow, IssueRow


def _to_domain(row: IssueRow) -> Issue:
    duplicate = (
        DuplicateLink(row.duplicate_of_repository, row.duplicate_of_number)
        if row.duplicate_of_repository is not None and row.duplicate_of_number is not None
        else None
    )
    return Issue(
        repository=row.repository,
        number=row.number,
        title=row.title,
        body=row.body,
        author=row.author,
        author_association=row.author_association,
        state=row.state,
        state_reason=row.state_reason,
        created_at=row.created_at,
        closed_at=row.closed_at,
        labels=tuple(row.labels),
        duplicate_of=duplicate,
        comments=tuple(
            Comment(c.author, c.author_association, c.body, c.created_at) for c in row.comments
        ),
        comment_count=row.comment_count,
    )


def _copy_into(row: IssueRow, issue: Issue) -> None:
    row.repository = issue.repository
    row.number = issue.number
    row.title = issue.title
    row.body = issue.body
    row.author = issue.author
    row.author_association = issue.author_association
    row.state = issue.state
    row.state_reason = issue.state_reason
    row.created_at = issue.created_at
    row.closed_at = issue.closed_at
    row.labels = list(issue.labels)
    row.duplicate_of_repository = issue.duplicate_of.repository if issue.duplicate_of else None
    row.duplicate_of_number = issue.duplicate_of.number if issue.duplicate_of else None
    row.comment_count = issue.comment_count
    row.comments = [
        CommentRow(
            position=position,
            author=c.author,
            author_association=c.author_association,
            body=c.body,
            created_at=c.created_at,
        )
        for position, c in enumerate(issue.comments)
    ]


class PostgresIssueRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def upsert(self, issues: Sequence[Issue]) -> int:
        """Insert new issues and replace existing ones (comments included)."""
        by_repository: dict[str, list[Issue]] = defaultdict(list)
        for issue in issues:
            by_repository[issue.repository].append(issue)
        with self._sessions.begin() as session:
            for repository, batch in by_repository.items():
                numbers = [issue.number for issue in batch]
                existing = {
                    row.number: row
                    for row in session.scalars(
                        select(IssueRow)
                        .where(IssueRow.repository == repository, IssueRow.number.in_(numbers))
                        .options(selectinload(IssueRow.comments))
                    )
                }
                for issue in batch:
                    row = existing.get(issue.number)
                    if row is None:
                        row = IssueRow()
                        session.add(row)
                        existing[issue.number] = row
                    _copy_into(row, issue)
        return len(issues)

    def get(self, repository: str, number: int) -> Issue | None:
        with self._sessions() as session:
            row = session.scalar(
                select(IssueRow)
                .where(IssueRow.repository == repository, IssueRow.number == number)
                .options(selectinload(IssueRow.comments))
            )
            return _to_domain(row) if row else None

    def list(self, repository: str, *, created_before: datetime | None = None) -> list[Issue]:
        query = (
            select(IssueRow)
            .where(IssueRow.repository == repository)
            .options(selectinload(IssueRow.comments))
            .order_by(IssueRow.created_at, IssueRow.number)
        )
        if created_before is not None:
            query = query.where(IssueRow.created_at < created_before)
        with self._sessions() as session:
            return [_to_domain(row) for row in session.scalars(query)]

    def count(self, repository: str) -> int:
        with self._sessions() as session:
            total = session.scalar(
                select(func.count()).select_from(IssueRow).where(IssueRow.repository == repository)
            )
            return int(total or 0)
