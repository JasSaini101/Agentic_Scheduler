"""
Mock data for the week. This is what gets replaced by real APIs later.
Each piece maps to a future data source:
  - weather       → OpenWeatherMap hourly forecast
  - gym_busyness  → Google Maps Popular Times scrape / known patterns
  - sleep_score   → Oura / Garmin / manual morning input
  - hard_events   → Google Calendar API
"""

from datetime import date, time, timedelta

# ── Week definition ───────────────────────────────────────────────────────────
# Start date for the schedule — always the Monday of the current week.
# Use current_week_start() at call time; a long-lived server process would
# otherwise freeze the week at whatever it was when the module was imported.
def current_week_start() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())

# Legacy alias — frozen at import time; prefer current_week_start()
WEEK_START = current_week_start()

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# ── Weather mock ──────────────────────────────────────────────────────────────
# Future: hourly dict keyed by (day, hour) from OpenWeatherMap
# Conditions: "clear" | "cloudy" | "rain" | "storm"
# temp_f: feels-like in Fahrenheit
WEATHER = {
    "Monday":    {"condition": "clear",  "temp_f": 78},
    "Tuesday":   {"condition": "rain",   "temp_f": 65},
    "Wednesday": {"condition": "clear",  "temp_f": 82},
    "Thursday":  {"condition": "cloudy", "temp_f": 74},
    "Friday":    {"condition": "clear",  "temp_f": 80},
    "Saturday":  {"condition": "storm",  "temp_f": 68},
    "Sunday":    {"condition": "clear",  "temp_f": 76},
}

# ── Gym busyness mock ─────────────────────────────────────────────────────────
# Future: scraped from Google Maps Popular Times
# Scale 0.0 (empty) → 1.0 (packed)
# Keyed by (day, hour) where hour is the START of the slot
GYM_BUSYNESS = {
    ("Monday",    6):  0.3,
    ("Monday",    7):  0.5,
    ("Monday",   17):  0.9,
    ("Monday",   18):  1.0,
    ("Tuesday",   6):  0.2,
    ("Tuesday",   7):  0.4,
    ("Tuesday",  17):  0.8,
    ("Tuesday",  18):  0.9,
    ("Wednesday", 6):  0.3,
    ("Wednesday", 7):  0.5,
    ("Wednesday",17):  0.7,
    ("Wednesday",18):  0.8,
    ("Thursday",  6):  0.2,
    ("Thursday",  7):  0.4,
    ("Thursday", 17):  0.6,
    ("Thursday", 18):  0.7,
    ("Friday",    6):  0.4,
    ("Friday",    7):  0.6,
    ("Friday",   17):  0.5,
    ("Friday",   18):  0.6,
    ("Saturday",  8):  0.7,
    ("Saturday",  9):  0.8,
    ("Saturday", 10):  0.9,
    ("Sunday",    8):  0.3,
    ("Sunday",    9):  0.4,
    ("Sunday",   10):  0.5,
}

# ── Sleep / body rest mock ────────────────────────────────────────────────────
# Future: Oura Ring API or a morning form (1–10 scale)
# Represents how rested you feel at the START of that day
SLEEP_SCORES = {
    "Monday":    8,
    "Tuesday":   6,
    "Wednesday": 9,
    "Thursday":  7,
    "Friday":    5,   # rough night
    "Saturday":  8,
    "Sunday":    9,
}

# ── Hard events (immovable blocks) ────────────────────────────────────────────
# Future: Google Calendar API
# Each event: {day, start_hour, end_hour, label, type}
HARD_EVENTS = [
    {"day": "Monday",    "start_hour": 9,  "end_hour": 17, "label": "Work",                  "type": "work"},
    {"day": "Tuesday",   "start_hour": 9,  "end_hour": 17, "label": "Work",                  "type": "work"},
    {"day": "Tuesday",   "start_hour": 19, "end_hour": 21, "label": "Family dinner",          "type": "social"},
    {"day": "Wednesday", "start_hour": 9,  "end_hour": 17, "label": "Work",                  "type": "work"},
    {"day": "Wednesday", "start_hour": 14, "end_hour": 15, "label": "1:1 with manager",      "type": "meeting"},
    {"day": "Thursday",  "start_hour": 9,  "end_hour": 17, "label": "Work",                  "type": "work"},
    {"day": "Thursday",  "start_hour": 20, "end_hour": 22, "label": "World Cup: USA vs BRA", "type": "worldcup"},
    {"day": "Friday",    "start_hour": 9,  "end_hour": 17, "label": "Work",                  "type": "work"},
    {"day": "Saturday",  "start_hour": 12, "end_hour": 14, "label": "Friend's BBQ",          "type": "social"},
]

# ── Candidate gym slots ───────────────────────────────────────────────────────
# Hours we'd ever consider going to the gym (24h)
GYM_CANDIDATE_HOURS = [6, 7, 17, 18]

# ── Candidate outdoor sport slots ────────────────────────────────────────────
# Hours we'd ever play tennis or basketball outdoors
SPORT_CANDIDATE_HOURS = [8, 9, 10, 16, 17]

# ── Fill activities (priority-ordered) ───────────────────────────────────────
# Placed by fill_agent into remaining gaps after all other agents run.
# duration: hours | padding: buffer to leave after the activity | reason: display hint
FILL_ACTIVITIES = [
    {"label": "Meal prep",         "duration": 1.5, "padding": 0.5, "reason": "Weekly batch cook"},
    {"label": "Reading",           "duration": 1.0, "padding": 0.0, "reason": "30-day reading goal"},
    {"label": "Grocery run",       "duration": 1.0, "padding": 0.5, "reason": "Restock for the week"},
    {"label": "Walk / light jog",  "duration": 1.0, "padding": 0.0, "reason": "Active recovery"},
    {"label": "Personal admin",    "duration": 1.0, "padding": 0.0, "reason": "Emails, errands, finances"},
]
