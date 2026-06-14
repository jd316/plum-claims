"""baseline: capture the current `claims` table

Revision ID: 0001
Revises:
Create Date: 2026-06-13

This migration captures the CURRENT schema — the `claims` table exactly as the
ClaimRow model defines it. It is written idempotently (create the table only if it
does not already exist) so an existing dev/prod DB that already has `claims`
(created by the original `create_all`) lands cleanly at this revision: running
`alembic upgrade head` against such a DB is a no-op for the table itself, and
`alembic stamp 0001` is equally valid to mark an existing DB as baselined.
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    if _has_table("claims"):
        return  # existing DB already at baseline — nothing to do
    op.create_table(
        "claims",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("member_id", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("blocked", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("approved_amount", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("submission", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("claims")
