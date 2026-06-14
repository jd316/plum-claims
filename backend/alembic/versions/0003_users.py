"""additive: users table for self-issued JWT auth + RBAC

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-13

ADDITIVE and non-breaking. Creates a single `users` table (id, username, password_hash,
role, member_id, created_at) with a unique index on username. It does NOT touch any
existing table. The create is guarded (IF NOT EXISTS semantics via inspector) so the
migration is a clean no-op against a DB where `users` was already materialised by
SQLAlchemy `create_all` (init_db), and applies fresh otherwise.

This table is only meaningfully used when settings.auth_enabled is True; with auth off
the RBAC dependencies are permissive no-ops and never read it.
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return _inspector().has_table(name)


def _existing_indices(table: str) -> set[str]:
    if not _has_table(table):
        return set()
    return {ix["name"] for ix in _inspector().get_indexes(table)}


def upgrade() -> None:
    if not _has_table("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("username", sa.String(), nullable=False),
            sa.Column("password_hash", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("member_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("username", name="uq_users_username"),
        )
    if "ix_users_username" not in _existing_indices("users"):
        op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    if _has_table("users"):
        op.drop_table("users")  # drops its indices/constraints with it
