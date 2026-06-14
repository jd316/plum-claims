"""Serialize datetimes as unambiguous UTC ISO strings for the API.

`created_at` columns are `timestamp without time zone` but always hold UTC (the
ORM default is `datetime.now(timezone.utc)`). Read back they come out NAIVE, so a
bare `.isoformat()` emits no offset — and a marker-less datetime string is parsed
as LOCAL time by browsers (`new Date(...)`), shifting every displayed time by the
client's UTC offset (e.g. +5:30 in IST). Stamp the value as UTC on the way out so
the API contract is unambiguous and clients render it in their own timezone
correctly.
"""
from datetime import datetime, timezone


def iso_utc(dt: datetime | None) -> str | None:
    """ISO-8601 string with an explicit UTC offset, or None. A naive value is
    assumed to be UTC (our storage convention) and marked as such."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
