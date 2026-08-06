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

from scheduler.data.mock_week import DAYS, local_today


def _day_names_by_date() -> dict[str, str]:
    """Map ISO date → day name for the rolling window [today, today+6]."""
    today = local_today()
    return {
        (today + timedelta(days=i)).isoformat(): DAYS[(today + timedelta(days=i)).weekday()]
        for i in range(7)
    }


def _get_service(user_email: str | None = None):
    from scheduler.data.providers.google_auth import get_service
    return get_service(user_email)


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


def fetch_hard_events(user_email: str | None = None,
                      calendar_id: str | None = None) -> list[dict]:
    """
    Returns a list of hard events for WEEK_START through WEEK_START+7 days.
    Raises RuntimeError if credentials are missing or the API call fails.
    """
    if calendar_id is None:
        calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
    service = _get_service(user_email)

    window_start = local_today()
    day_names = _day_names_by_date()
    window_end = window_start + timedelta(days=7)
    time_min = datetime(window_start.year, window_start.month, window_start.day,
                        tzinfo=timezone.utc).isoformat()
    time_max = datetime(window_end.year, window_end.month, window_end.day,
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
