"""ORM tables. The schema is created and changed only through Alembic migrations."""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class CommentRow(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    author: Mapped[str] = mapped_column(String(64))
    author_association: Mapped[str] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IssueRow(Base):
    __tablename__ = "issues"
    __table_args__ = (
        UniqueConstraint("repository", "number", name="uq_issues_repository_number"),
        Index("ix_issues_repository_created_at", "repository", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository: Mapped[str] = mapped_column(String(200))
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(64))
    author_association: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16))
    state_reason: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    labels: Mapped[list[str]] = mapped_column(ARRAY(String(100)))
    duplicate_of_repository: Mapped[str | None] = mapped_column(String(200))
    duplicate_of_number: Mapped[int | None] = mapped_column(Integer)
    comment_count: Mapped[int] = mapped_column(Integer)

    comments: Mapped[list[CommentRow]] = relationship(
        order_by=CommentRow.position, cascade="all, delete-orphan", passive_deletes=True
    )
