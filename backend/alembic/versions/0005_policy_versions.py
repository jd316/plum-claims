"""additive: policy_versions table for the policy-as-code studio

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-13

ADDITIVE and non-breaking. Creates a single `policy_versions` table
(id, version_no, label, policy_json JSON, is_active, created_by, created_at) with an
index on version_no. It does NOT touch any existing table. The studio seeds v1 == the
current policy_terms.json (active) at runtime via seed_initial_version(); this migration
only materialises the table.

The create is guarded (inspector-based IF NOT EXISTS) so it is a clean no-op against a
DB where SQLAlchemy create_all already built the table from the shared Base, and applies
fresh otherwise.
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
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
    if not _has_table("policy_versions"):
        op.create_table(
            "policy_versions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column("label", sa.String(), nullable=True),
            sa.Column("policy_json", sa.JSON(), nullable=False),
            sa.Column("policy_text", sa.String(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    if "ix_policy_versions_version_no" not in _existing_indices("policy_versions"):
        op.create_index("ix_policy_versions_version_no", "policy_versions", ["version_no"])


def downgrade() -> None:
    if _has_table("policy_versions"):
        op.drop_table("policy_versions")  # drops its index with it
