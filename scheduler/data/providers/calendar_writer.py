"""
Calendar Writer
───────────────
Writes ScheduleEntry objects to Google Calendar.

On each run:
  1. Deletes all existing events tagged with source="agentic_scheduler" for this week
  2. Creates one event per ScheduleEntry

Events are tagged via extendedProperties so future runs can clean them up.
OAuth scope required: https://www.googleapis.com/auth/calendar
(Delete token.json and re-run to re-authorize if you previously used readonly scope.)
"""

from datetime import datetime, timedelta, timezone

from datetime import date

from scheduler.data.mock_week import date_for_day
from scheduler.models import ScheduleEntry
from scheduler.data.providers.google_auth import get_service as _get_service

_SOURCE_TAG = "agentic_scheduler"


def _day_to_date(day_name: str) -> str:
    """ISO date for the next occurrence of day_name (rolling 7-day window)."""
    return date_for_day(day_name).isoformat()


def _event_tz() -> str:
    """Timezone written on Google Calendar events — follows SCHEDULER_TZ."""
    import os
    return os.getenv("SCHEDULER_TZ", "America/New_York")


def _hhmm(hour: float) -> str:
    """Fractional hour → 'HH:MM' (9.5 → '09:30')."""
    h = int(hour)
    m = int(round((hour - h) * 60))
    return f"{h:02d}:{m:02d}"


def _delete_scheduler_events(service, calendar_id: str) -> None:
    """Delete scheduler-tagged events in the upcoming 7-day window."""
    from scheduler.data.mock_week import local_today
    window_start = local_today()
    week_start_dt = datetime.combine(window_start, datetime.min.time()).isoformat() + "Z"
    week_end = window_start + timedelta(days=7)
    week_end_dt = datetime.combine(week_end, datetime.min.time()).isoformat() + "Z"

    page_token = None
    deleted = 0
    while True:
        events = service.events().list(
            calendarId=calendar_id,
            timeMin=week_start_dt,
            timeMax=week_end_dt,
            privateExtendedProperty=f"source={_SOURCE_TAG}",
            pageToken=page_token,
        ).execute()

        for event in events.get("items", []):
            service.events().delete(calendarId=calendar_id, eventId=event["id"]).execute()
            deleted += 1

        page_token = events.get("nextPageToken")
        if not page_token:
            break

    if deleted:
        print(f"[calendar_writer] Deleted {deleted} existing scheduler event(s)")


def write_schedule(entries: list[ScheduleEntry], calendar_id: str = "primary", replace: bool = True) -> None:
    """Write ScheduleEntry list to Google Calendar.

    replace=True clears prior scheduler-created events first (full rebuild);
    replace=False appends without deleting (e.g. adding one event mid-week).
    hard_block entries mirror events that already exist in the calendar —
    writing them would duplicate them, so they are skipped."""
    service = _get_service()
    if replace:
        _delete_scheduler_events(service, calendar_id)

    to_write = [e for e in entries if e.category != "hard_block"]
    skipped = len(entries) - len(to_write)
    if skipped:
        print(f"[calendar_writer] Skipped {skipped} existing calendar event(s) (hard blocks)")

    created = 0
    for entry in to_write:
        date_str = _day_to_date(entry.day)
        start_dt = f"{date_str}T{_hhmm(entry.start_hour)}:00"
        end_dt = f"{date_str}T{_hhmm(entry.end_hour)}:00"

        description = "\n".join(entry.reasons) if entry.reasons else ""

        event = {
            "summary": entry.label,
            "description": description,
            "start": {"dateTime": start_dt, "timeZone": _event_tz()},
            "end": {"dateTime": end_dt, "timeZone": _event_tz()},
            "extendedProperties": {
                "private": {
                    "source": _SOURCE_TAG,
                    "category": entry.category,
                }
            },
        }

        service.events().insert(calendarId=calendar_id, body=event).execute()
        created += 1

    print(f"[calendar_writer] Created {created} event(s) in Google Calendar")
