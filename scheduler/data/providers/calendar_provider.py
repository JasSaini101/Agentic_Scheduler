"""
Calendar Provider — Google Calendar API
─────────────────────────────────────────
Auth: OAuth2 via google-auth-oauthlib.
  - On first run, opens a browser to complete the OAuth flow and saves token.json.
  - On subsequent runs, token.json is refreshed automatically.

Env var optional: GOOGLE_CALENDAR_ID (default: "primary")
Credentials file: credentials.json must be present in the repo root
  (download from Google Cloud Console → APIs & Services → Credentials).

Returns a list matching the HARD_EVENTS mock schema:
  [{"day": str, "start_hour": int, "end_hour": float, "label": str, "type": str}]
"""

import os
from datetime import datetime, timezone, timedelta

from scheduler.data.mock_week import DAYS, current_week_start


def _day_names_by_date() -> dict[str, str]:
    week_start = current_week_start()
    return {(week_start + timedelta(days=i)).isoformat(): name for i, name in enumerate(DAYS)}


def _get_service():
    from scheduler.data.providers.google_auth import get_service
    return get_service()


def _event_type(event: dict) -> str:
    """Derive a type string from the event's colorId or summary keywords."""
    summary = event.get("summary", "").lower()
    if any(k in summary for k in ("work", "office", "shift")):
        return "work"
    if any(k in summary for k in ("meeting", "sync", "standup", "1:1")):
        return "meeting"
    if any(k in summary for k in ("dinner", "lunch", "family", "party", "bbq")):
        return "social"
    return "calendar"


def fetch_hard_events() -> list[dict]:
    """
    Returns a list of hard events for WEEK_START through WEEK_START+7 days.
    Raises RuntimeError if credentials are missing or the API call fails.
    """
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
    service = _get_service()

    week_start = current_week_start()
    day_names = _day_names_by_date()
    week_end = week_start + timedelta(days=7)
    time_min = datetime(week_start.year, week_start.month, week_start.day,
                        tzinfo=timezone.utc).isoformat()
    time_max = datetime(week_end.year, week_end.month, week_end.day,
                        tzinfo=timezone.utc).isoformat()

    result = service.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = []
    for event in result.get("items", []):
        start = event.get("start", {})
        end = event.get("end", {})

        # Skip all-day events (they have "date" not "dateTime")
        if "dateTime" not in start:
            continue

        start_dt = datetime.fromisoformat(start["dateTime"])
        end_dt = datetime.fromisoformat(end["dateTime"])

        date_key = start_dt.date().isoformat()
        day_name = day_names.get(date_key)
        if not day_name:
            continue

        events.append({
            "day": day_name,
            "start_hour": start_dt.hour + start_dt.minute / 60,
            "end_hour": end_dt.hour + end_dt.minute / 60,
            "label": event.get("summary", "Busy"),
            "type": _event_type(event),
        })

    return events
