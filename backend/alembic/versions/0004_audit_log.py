"""additive: append-only audit_log table for decided claims

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-13

ADDITIVE and non-breaking. Creates a single append-only `audit_log` table
(id, claim_id, actor, action, decision_status, approved_amount, reason_codes JSON,
created_at) with an index on claim_id. It does NOT touch any existing table and stores
NO PHI — only the non-PHI decision summary — so it is safe to retain after a retention
sweep anonymizes the referenced claims.

The create is guarded (IF NOT EXISTS semantics via inspector) so the migration is a clean
no-op against a DB where `audit_log` was already materialised by SQLAlchemy `create_all`
(init_db, which sees AuditLogRow on the shared Base), and applies fresh otherwise.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
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
    if not _has_table("audit_log"):
        op.create_table(
            "audit_log",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("claim_id", sa.String(), nullable=False),
            sa.Column("actor", sa.String(), nullable=False),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("decision_status", sa.String(), nullable=True),
            sa.Column("approved_amount", sa.Float(), nullable=True),
            sa.Column("reason_codes", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    if "ix_audit_log_claim_id" not in _existing_indices("audit_log"):
        op.create_index("ix_audit_log_claim_id", "audit_log", ["claim_id"])


def downgrade() -> None:
    if _has_table("audit_log"):
        op.drop_table("audit_log")  # drops its index with it
