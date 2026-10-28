"""Issue-tracker entities, independent of where the issues come from."""

from dataclasses import dataclass, field
from datetime import datetime

MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


@dataclass(frozen=True)
class Comment:
    author: str
    author_association: str
    body: str
    created_at: datetime

    @property
    def by_maintainer(self) -> bool:
        return self.author_association in MAINTAINER_ASSOCIATIONS


@dataclass(frozen=True)
class DuplicateLink:
    """The issue this one was closed as a duplicate of."""

    repository: str
    number: int


@dataclass(frozen=True)
class Issue:
    repository: str
    number: int
    title: str
    body: str
    author: str
    author_association: str
    state: str
    state_reason: str | None
    created_at: datetime
    closed_at: datetime | None
    labels: tuple[str, ...] = ()
    duplicate_of: DuplicateLink | None = None
    comments: tuple[Comment, ...] = field(default=())
    comment_count: int = 0

    @property
    def comments_truncated(self) -> bool:
        """True when the source returned fewer comments than the issue has."""
        return self.comment_count > len(self.comments)
