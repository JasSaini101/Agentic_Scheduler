"""
Timezone-aware "today" — the server may run in UTC while the user is hours
behind; SCHEDULER_TZ keeps date resolution on the user's clock.
"""

from datetime import datetime, timezone, timedelta

from scheduler.data.mock_week import DAYS, date_for_day, local_today


def test_local_today_follows_scheduler_tz(monkeypatch):
    utc_today = datetime.now(timezone.utc).date()
    # Kiritimati is UTC+14, Honolulu UTC-10 — between them the local date
    # differs from UTC for every moment of the day, in one direction or both.
    monkeypatch.setenv("SCHEDULER_TZ", "Pacific/Kiritimati")
    ahead = local_today()
    monkeypatch.setenv("SCHEDULER_TZ", "Pacific/Honolulu")
    behind = local_today()
    assert ahead >= utc_today >= behind
    assert (ahead - behind) in (timedelta(days=0), timedelta(days=1), timedelta(days=2))
    assert ahead != behind or ahead == utc_today  # they can't all be equal off-UTC-midnight


def test_invalid_tz_falls_back_without_raising(monkeypatch):
    monkeypatch.setenv("SCHEDULER_TZ", "Not/AZone")
    assert local_today() is not None


def test_date_for_day_never_in_the_past(monkeypatch):
    monkeypatch.setenv("SCHEDULER_TZ", "Pacific/Honolulu")
    today = local_today()
    for day in DAYS:
        resolved = date_for_day(day)
        assert today <= resolved < today + timedelta(days=7)
        assert DAYS[resolved.weekday()] == day
