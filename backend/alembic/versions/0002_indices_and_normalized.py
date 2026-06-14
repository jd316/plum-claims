"""additive: indices on claims + normalized documents/trace_entries tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-13

ADDITIVE and non-breaking. This migration:
  * adds indices on claims(member_id, created_at, status, category)
  * creates two normalized read-model tables, `documents` and `trace_entries`,
    each FK→claims.id with an index on claim_id.

It does NOT drop or alter the JSONB `submission`/`result` columns — they remain the
source of truth for every existing reader. Every create is guarded (IF NOT EXISTS
semantics via inspector) so the migration is a clean no-op against a DB where these
objects were already materialised by SQLAlchemy `create_all` (init_db), and applies
fresh against a DB that only has the baseline `claims` table.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_CLAIMS_INDICES = {
    "ix_claims_member_id": "member_id",
    "ix_claims_created_at": "created_at",
    "ix_claims_status": "status",
    "ix_claims_category": "category",
}


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return _inspector().has_table(name)


def _existing_indices(table: str) -> set[str]:
    if not _has_table(table):
        return set()
    return {ix["name"] for ix in _inspector().get_indexes(table)}


def upgrade() -> None:
    # --- indices on the existing claims table (additive) ---
    have = _existing_indices("claims")
    for name, col in _CLAIMS_INDICES.items():
        if name not in have:
            op.create_index(name, "claims", [col])

    # --- normalized documents table ---
    if not _has_table("documents"):
        op.create_table(
            "documents",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("claim_id", sa.String(),
                      sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
            sa.Column("file_id", sa.String(), nullable=True),
            sa.Column("file_name", sa.String(), nullable=True),
            sa.Column("doc_type", sa.String(), nullable=True),
            sa.Column("stored_path", sa.String(), nullable=True),
            sa.Column("content_type", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    if "ix_documents_claim_id" not in _existing_indices("documents"):
        op.create_index("ix_documents_claim_id", "documents", ["claim_id"])

    # --- normalized trace_entries table ---
    if not _has_table("trace_entries"):
        op.create_table(
            "trace_entries",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("claim_id", sa.String(),
                      sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=True),
            sa.Column("step", sa.String(), nullable=True),
            sa.Column("agent", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("summary", sa.String(), nullable=True),
            sa.Column("degraded", sa.Boolean(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    if "ix_trace_entries_claim_id" not in _existing_indices("trace_entries"):
        op.create_index("ix_trace_entries_claim_id", "trace_entries", ["claim_id"])


def downgrade() -> None:
    if _has_table("trace_entries"):
        op.drop_table("trace_entries")  # drops its indices with it
    if _has_table("documents"):
        op.drop_table("documents")
    have = _existing_indices("claims")
    for name in _CLAIMS_INDICES:
        if name in have:
            op.drop_index(name, table_name="claims")
