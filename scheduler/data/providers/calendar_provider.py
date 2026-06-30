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
from pathlib import Path

from scheduler.data.mock_week import WEEK_START, DAYS

_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
_TOKEN_PATH = Path("token.json")
_CREDENTIALS_PATH = Path("credentials.json")
_DAY_NAME = {(WEEK_START + timedelta(days=i)).isoformat(): name for i, name in enumerate(DAYS)}


def _get_service():
    """Return an authenticated Google Calendar service object."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not _CREDENTIALS_PATH.exists():
                raise RuntimeError(
                    "credentials.json not found. Download it from Google Cloud Console "
                    "and place it in the repo root."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(_CREDENTIALS_PATH), _SCOPES)
            creds = flow.run_local_server(port=0)
        _TOKEN_PATH.write_text(creds.to_json())

    return build("calendar", "v3", credentials=creds)


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

    week_end = WEEK_START + timedelta(days=7)
    time_min = datetime(WEEK_START.year, WEEK_START.month, WEEK_START.day,
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
        day_name = _DAY_NAME.get(date_key)
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
