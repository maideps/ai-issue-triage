"""Review queue: triage runs and reviewer feedback (feat-014).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "triage_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repository", sa.String(200), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("needs_attention", sa.Boolean(), nullable=False),
        sa.Column("proposal_text", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.ARRAY(sa.String(300)), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("final_text", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("repository", "number", name="uq_triage_runs_repository_number"),
    )
    op.create_index("ix_triage_runs_status_updated", "triage_runs", ["status", "updated_at"])
    op.create_table(
        "review_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("triage_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("final_text", sa.Text(), nullable=True),
        sa.Column("reviewer", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_review_feedback_run_id", "review_feedback", ["run_id"])


def downgrade() -> None:
    op.drop_table("review_feedback")
    op.drop_table("triage_runs")
