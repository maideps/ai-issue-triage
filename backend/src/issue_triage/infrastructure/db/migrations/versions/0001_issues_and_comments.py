"""Issues and comments (feat-003).

Revision ID: 0001
Revises:
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repository", sa.String(200), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author", sa.String(64), nullable=False),
        sa.Column("author_association", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("state_reason", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("labels", postgresql.ARRAY(sa.String(100)), nullable=False),
        sa.Column("duplicate_of_repository", sa.String(200), nullable=True),
        sa.Column("duplicate_of_number", sa.Integer(), nullable=True),
        sa.Column("comment_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("repository", "number", name="uq_issues_repository_number"),
    )
    op.create_index("ix_issues_repository_created_at", "issues", ["repository", "created_at"])
    op.create_table(
        "comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "issue_id",
            sa.Integer(),
            sa.ForeignKey("issues.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("author", sa.String(64), nullable=False),
        sa.Column("author_association", sa.String(32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_comments_issue_id", "comments", ["issue_id"])


def downgrade() -> None:
    op.drop_table("comments")
    op.drop_table("issues")
