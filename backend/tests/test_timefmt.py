from datetime import datetime, timezone

from app.services.timefmt import iso_utc


def test_naive_value_is_marked_utc():
    # A naive datetime (our DB read-back convention) must serialize WITH an explicit
    # UTC offset, so browsers don't misparse it as local time.
    s = iso_utc(datetime(2026, 6, 14, 21, 28, 14))
    assert s is not None and (s.endswith("+00:00") or s.endswith("Z"))
    assert s.startswith("2026-06-14T21:28:14")


def test_none_passes_through():
    assert iso_utc(None) is None


def test_aware_value_keeps_its_offset():
    aware = datetime(2026, 6, 14, 21, 28, 14, tzinfo=timezone.utc)
    assert iso_utc(aware) == aware.isoformat()
    assert iso_utc(aware).endswith("+00:00")
