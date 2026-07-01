"""
Standalone test for the Google Calendar API integration.
Run from the repo root:  python test_calendar.py

Checks:
  1. credentials.json is present
  2. OAuth flow completes and token.json is saved
  3. Raw API call succeeds and returns events for this week
  4. Event parsing into the scheduler's HARD_EVENTS schema is correct
"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

SCOPES       = ["https://www.googleapis.com/auth/calendar.readonly"]
TOKEN_PATH   = Path("token.json")
CREDS_PATH   = Path("credentials.json")
DAYS         = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

def this_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())

def sep(title=""):
    print("\n" + "─" * 52)
    if title:
        print(f"  {title}")
        print("─" * 52)

# ── Check 1: credentials.json present ────────────────────
sep("CHECK 1 — credentials.json")
if not CREDS_PATH.exists():
    print("  FAIL: credentials.json not found in repo root.")
    print("  Download it from GCP Console → APIs & Services → Credentials")
    print("  (OAuth 2.0 Client ID → Desktop app → Download JSON → rename to credentials.json)")
    sys.exit(1)
print("  OK  — credentials.json found")

# ── Check 2: OAuth flow / token ───────────────────────────
sep("CHECK 2 — OAuth authentication")
try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError as e:
    print(f"  FAIL: Missing dependency — {e}")
    print("  Run: pip install google-api-python-client google-auth-oauthlib")
    sys.exit(1)

creds = None
if TOKEN_PATH.exists():
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    print("  Found existing token.json")

if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
        print("  Token expired — refreshing...")
        creds.refresh(Request())
        print("  OK  — Token refreshed")
    else:
        print("  No valid token found — starting OAuth flow...")
        print("  A browser window will open. Log in and grant calendar access.")
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
        creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())
        print("  OK  — Authorized. token.json saved for future runs.")
else:
    print("  OK  — Token is valid")

# ── Check 3: Raw API call ─────────────────────────────────
sep("CHECK 3 — Raw API call")
try:
    service = build("calendar", "v3", credentials=creds)
except Exception as e:
    print(f"  FAIL: Could not build Calendar service — {e}")
    sys.exit(1)

WEEK_START = this_monday()
week_end   = WEEK_START + timedelta(days=7)
time_min   = datetime(WEEK_START.year, WEEK_START.month, WEEK_START.day, tzinfo=timezone.utc).isoformat()
time_max   = datetime(week_end.year,   week_end.month,   week_end.day,   tzinfo=timezone.utc).isoformat()

try:
    result = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
except Exception as e:
    print(f"  FAIL: API call failed — {e}")
    sys.exit(1)

items = result.get("items", [])
print(f"  OK  — API call succeeded")
print(f"  Week: {WEEK_START}  →  {week_end}")
print(f"  Events returned: {len(items)}")

# ── Check 4: Raw event listing ────────────────────────────
sep("CHECK 4 — Raw events this week")
if not items:
    print("  No events found for this week on your primary calendar.")
else:
    for event in items:
        start = event.get("start", {})
        end   = event.get("end", {})
        title = event.get("summary", "(no title)")
        if "dateTime" in start:
            s = datetime.fromisoformat(start["dateTime"])
            e = datetime.fromisoformat(end["dateTime"])
            print(f"  {s.strftime('%A'):10s} {s.strftime('%I:%M%p')}–{e.strftime('%I:%M%p')}  |  {title}")
        else:
            print(f"  All-day  |  {title}  (skipped — all-day events are ignored)")

# ── Check 5: Parsed scheduler output ─────────────────────
sep("CHECK 5 — Parsed output (what the scheduler sees)")
DAY_NAME = {(WEEK_START + timedelta(days=i)).isoformat(): name for i, name in enumerate(DAYS)}

parsed  = []
skipped = 0
for event in items:
    start = event.get("start", {})
    end   = event.get("end", {})
    if "dateTime" not in start:
        skipped += 1
        continue
    start_dt = datetime.fromisoformat(start["dateTime"])
    end_dt   = datetime.fromisoformat(end["dateTime"])
    date_key = start_dt.date().isoformat()
    day_name = DAY_NAME.get(date_key)
    if not day_name:
        skipped += 1
        continue
    parsed.append({
        "day":        day_name,
        "start_hour": round(start_dt.hour + start_dt.minute / 60, 2),
        "end_hour":   round(end_dt.hour   + end_dt.minute   / 60, 2),
        "label":      event.get("summary", "Busy"),
    })

if not parsed:
    print("  No timed events found that map to this week's schedule.")
else:
    for e in parsed:
        print(f"  {e['day']:10s}  {e['start_hour']:5.2f}h – {e['end_hour']:5.2f}h  |  {e['label']}")

sep()
print(f"  PASS — {len(parsed)} event(s) parsed, {skipped} skipped (all-day or out of range).")
print("  These will replace the mock HARD_EVENTS when the scheduler runs.")
print()
