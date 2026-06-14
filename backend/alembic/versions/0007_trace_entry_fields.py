"""additive: full-fidelity trace_entries projection columns

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-15

ADDITIVE and non-breaking. Adds the previously-dropped trace fields to the normalized
`trace_entries` table so the decision trace is queryable in SQL at full fidelity:
model, input_tokens, output_tokens, failure_mode, confidence_delta, policy_refs (JSON),
detail (JSON). Each add is guarded (skip if the column already exists), so it is a clean
no-op against a DB where create_all already built the columns from the shared Base.
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_COLUMNS = [
    ("model", sa.String()),
    ("input_tokens", sa.Integer()),
    ("output_tokens", sa.Integer()),
    ("failure_mode", sa.String()),
    ("confidence_delta", sa.Float()),
    ("policy_refs", sa.JSON()),
    ("detail", sa.JSON()),
]


def _existing_columns(table: str) -> set[str]:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(table):
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("trace_entries"):
        return
    have = _existing_columns("trace_entries")
    for name, type_ in _COLUMNS:
        if name not in have:
            op.add_column("trace_entries", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    have = _existing_columns("trace_entries")
    for name, _ in _COLUMNS:
        if name in have:
            op.drop_column("trace_entries", name)
