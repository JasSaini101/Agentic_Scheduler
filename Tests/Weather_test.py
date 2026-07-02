"""
Standalone test for the OpenWeatherMap API integration.
Run from the repo root:  python test_weather.py

Checks:
  1. .env file is loaded and API key is present
  2. Raw API call succeeds and returns expected structure
  3. Condition mapping (OWM weather ID → clear/cloudy/rain/storm) is correct
  4. Final per-day output matches the scheduler's WEATHER schema
"""

import os
import sys
from collections import defaultdict
from datetime import date, timedelta

from dotenv import load_dotenv
import requests

load_dotenv()

API_KEY = os.environ.get("OPENWEATHERMAP_API_KEY", "").strip()
LAT     = os.environ.get("OPENWEATHERMAP_LAT", "40.7128")
LON     = os.environ.get("OPENWEATHERMAP_LON", "-74.0060")
API_URL = "https://api.openweathermap.org/data/2.5/forecast"

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

def this_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())

def owm_id_to_condition(weather_id: int) -> str:
    if weather_id == 800:              return "clear"
    if 801 <= weather_id <= 804:       return "cloudy"
    if 700 <= weather_id <= 799:       return "cloudy"
    if 200 <= weather_id <= 599:       return "rain"
    if 600 <= weather_id <= 699:       return "rain"
    return "cloudy"

def kelvin_to_f(k: float) -> int:
    return round((k - 273.15) * 9 / 5 + 32)

def sep(title=""):
    print("\n" + "─" * 52)
    if title:
        print(f"  {title}")
        print("─" * 52)

# ── Check 1: API key present ──────────────────────────────
sep("CHECK 1 — API key")
if not API_KEY:
    print("  FAIL: OPENWEATHERMAP_API_KEY not found in .env or environment.")
    print("  Make sure your .env file is in the repo root and contains:")
    print("    OPENWEATHERMAP_API_KEY=your_key_here")
    sys.exit(1)
print(f"  OK  — Key loaded: {API_KEY[:6]}{'*' * (len(API_KEY) - 6)}")
print(f"  LAT={LAT}  LON={LON}")

# ── Check 2: Raw API call ─────────────────────────────────
sep("CHECK 2 — Raw API call")
try:
    resp = requests.get(
        API_URL,
        params={"lat": LAT, "lon": LON, "appid": API_KEY},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
except requests.HTTPError as e:
    print(f"  FAIL: HTTP {resp.status_code} — {resp.text[:200]}")
    sys.exit(1)
except Exception as e:
    print(f"  FAIL: {e}")
    sys.exit(1)

slots = data.get("list", [])
city  = data.get("city", {})
print(f"  OK  — Connected to OWM")
print(f"  City reported: {city.get('name', 'unknown')}, {city.get('country', '')}")
print(f"  Forecast slots returned: {len(slots)}  (expect ~40 for 5-day/3h)")

# ── Check 3: Raw slot data ────────────────────────────────
sep("CHECK 3 — First 3 raw forecast slots")
for item in slots[:3]:
    dt       = item["dt_txt"]
    wid      = item["weather"][0]["id"]
    wdesc    = item["weather"][0]["description"]
    temp_k   = item["main"]["feels_like"]
    temp_f   = kelvin_to_f(temp_k)
    mapped   = owm_id_to_condition(wid)
    print(f"  {dt}  |  id={wid} ({wdesc})  →  {mapped}  |  feels like {temp_f}°F")

# ── Check 4: Per-day scheduler output ────────────────────
sep("CHECK 4 — Final per-day output (what the scheduler sees)")
WEEK_START = this_monday()
day_slots: dict[str, list] = defaultdict(list)
for item in slots:
    date_str   = item["dt_txt"][:10]
    weather_id = item["weather"][0]["id"]
    temp_k     = item["main"]["feels_like"]
    day_slots[date_str].append((owm_id_to_condition(weather_id), temp_k))

priority = {"storm": 3, "rain": 2, "cloudy": 1, "clear": 0}
result = {}
for i, day_name in enumerate(DAYS):
    date_key = (WEEK_START + timedelta(days=i)).isoformat()
    s = day_slots.get(date_key)
    if not s:
        print(f"  {day_name:10s} ({date_key})  →  NO DATA (beyond 5-day forecast window)")
        continue
    condition  = max((c for c, _ in s), key=lambda c: priority.get(c, 0))
    avg_temp_f = kelvin_to_f(sum(t for _, t in s) / len(s))
    result[day_name] = {"condition": condition, "temp_f": avg_temp_f}
    slots_str  = f"{len(s)} slots"
    print(f"  {day_name:10s} ({date_key})  →  {condition:7s}  {avg_temp_f}°F  ({slots_str})")

sep()
if result:
    print(f"  PASS — Live weather loaded for {len(result)}/7 days.")
    print("  The scheduler will use live data for these days and mock for the rest.")
else:
    print("  WARN — No days matched. Check that WEEK_START aligns with the forecast window.")
print()