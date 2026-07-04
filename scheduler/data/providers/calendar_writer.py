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

from scheduler.data.mock_week import DAYS, WEEK_START
from scheduler.models import ScheduleEntry
from scheduler.data.providers.google_auth import get_service as _get_service

_SOURCE_TAG = "agentic_scheduler"


def _day_to_date(day_name: str) -> str:
    """Return the ISO date string for the given day name in the current week."""
    idx = DAYS.index(day_name)
    return (WEEK_START + timedelta(days=idx)).isoformat()


def _delete_scheduler_events(service, calendar_id: str) -> None:
    """Delete all events tagged with source=agentic_scheduler for this week."""
    week_start_dt = datetime.combine(WEEK_START, datetime.min.time()).isoformat() + "Z"
    week_end = WEEK_START + timedelta(days=7)
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


def write_schedule(entries: list[ScheduleEntry], calendar_id: str = "primary") -> None:
    """Write ScheduleEntry list to Google Calendar, replacing any prior scheduler events."""
    service = _get_service()
    _delete_scheduler_events(service, calendar_id)

    created = 0
    for entry in entries:
        date_str = _day_to_date(entry.day)
        start_dt = f"{date_str}T{entry.start_hour:02d}:00:00"
        end_hour = int(entry.end_hour)
        end_min = int((entry.end_hour - end_hour) * 60)
        end_dt = f"{date_str}T{end_hour:02d}:{end_min:02d}:00"

        description = "\n".join(entry.reasons) if entry.reasons else ""

        event = {
            "summary": entry.label,
            "description": description,
            "start": {"dateTime": start_dt, "timeZone": "America/New_York"},
            "end": {"dateTime": end_dt, "timeZone": "America/New_York"},
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
