"""
Weather Provider — OpenWeatherMap 5-day/3-hour forecast API
────────────────────────────────────────────────────────────
Env var required: OPENWEATHERMAP_API_KEY
Optional:         OPENWEATHERMAP_LAT, OPENWEATHERMAP_LON (default: New York)

Returns a dict matching the WEATHER mock schema:
  {day: {"condition": str, "temp_f": int}}

OWM weather ID ranges → condition mapping:
  200-299  thunderstorm → "storm"
  300-599  drizzle/rain → "rain"
  600-699  snow         → "rain"   (treated as bad outdoor conditions)
  700-799  atmosphere   → "cloudy"
  800      clear        → "clear"
  801-804  clouds       → "cloudy"
"""

import os
import warnings
from collections import defaultdict
from datetime import timedelta

import requests

from scheduler.data.mock_week import WEEK_START, DAYS


_API_BASE = "https://api.openweathermap.org/data/2.5/forecast"
_DEFAULT_LAT = 40.7128   # New York
_DEFAULT_LON = -74.0060


def _owm_id_to_condition(weather_id: int) -> str:
    if weather_id == 800:
        return "clear"
    if 801 <= weather_id <= 804:
        return "cloudy"
    if 700 <= weather_id <= 799:
        return "cloudy"
    if 200 <= weather_id <= 599:
        return "rain"
    if weather_id == 212 or weather_id == 221 or (200 <= weather_id <= 232):
        return "storm"
    if 600 <= weather_id <= 699:
        return "rain"
    return "cloudy"


def _kelvin_to_f(k: float) -> int:
    return round((k - 273.15) * 9 / 5 + 32)


def fetch_weather() -> dict:
    """
    Returns {day: {"condition": str, "temp_f": int}} for the current week.
    Raises RuntimeError if the API call fails, so callers can fall back to mock.
    """
    api_key = os.environ.get("OPENWEATHERMAP_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENWEATHERMAP_API_KEY not set")

    lat = float(os.environ.get("OPENWEATHERMAP_LAT", _DEFAULT_LAT))
    lon = float(os.environ.get("OPENWEATHERMAP_LON", _DEFAULT_LON))

    resp = requests.get(
        _API_BASE,
        params={"lat": lat, "lon": lon, "appid": api_key},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    # Build a map of date string → list of (condition, temp_k) across 3h slots
    day_slots: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for item in data.get("list", []):
        date_str = item["dt_txt"][:10]   # "YYYY-MM-DD"
        weather_id = item["weather"][0]["id"]
        temp_k = item["main"]["feels_like"]
        day_slots[date_str].append((_owm_id_to_condition(weather_id), temp_k))

    result = {}
    for i, day_name in enumerate(DAYS):
        date_key = (WEEK_START + timedelta(days=i)).isoformat()
        slots = day_slots.get(date_key)
        if not slots:
            continue

        # Dominant condition: prefer worst (storm > rain > cloudy > clear)
        priority = {"storm": 3, "rain": 2, "cloudy": 1, "clear": 0}
        condition = max((c for c, _ in slots), key=lambda c: priority.get(c, 0))
        avg_temp_f = _kelvin_to_f(sum(t for _, t in slots) / len(slots))
        result[day_name] = {"condition": condition, "temp_f": avg_temp_f}

    return result
