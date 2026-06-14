"""self-documenting timestamps: created_at -> timestamptz

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-15

ADDITIVE and safe. Converts every `created_at` column from `timestamp without time
zone` (naive UTC) to `timestamp with time zone`, interpreting the existing values AS
UTC (our storage convention — the ORM default is datetime.now(timezone.utc)). The data
is unchanged; only the column type becomes self-documenting.

No-op against a DB whose columns SQLAlchemy create_all already built as timestamptz
(the models now declare DateTime(timezone=True)) — each ALTER is guarded by an
inspector check, so this applies only where a column is still naive. Postgres-specific;
returns early on other dialects (dev/test create_all builds the right type directly).
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_TABLES = ["claims", "documents", "users", "trace_entries", "audit_log", "policy_versions"]


def _column_is_tzaware(table: str, column: str) -> bool | None:
    """True/False if the column exists (tz-aware?), None if table/column is absent."""
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(table):
        return None
    for c in insp.get_columns(table):
        if c["name"] == column:
            return bool(getattr(c["type"], "timezone", False))
    return None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for t in _TABLES:
        if _column_is_tzaware(t, "created_at") is False:  # exists and naive → convert
            op.execute(sa.text(
                f"ALTER TABLE {t} ALTER COLUMN created_at TYPE timestamptz "
                "USING created_at AT TIME ZONE 'UTC'"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for t in _TABLES:
        if _column_is_tzaware(t, "created_at"):  # exists and tz-aware → back to naive UTC
            op.execute(sa.text(
                f"ALTER TABLE {t} ALTER COLUMN created_at TYPE timestamp "
                "USING created_at AT TIME ZONE 'UTC'"))
