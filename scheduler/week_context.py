"""
Week Context Builder
─────────────────────
Single entry point that assembles the full context dict for the orchestrator.
Each data source is attempted live first; on any failure it falls back to mock
data and prints a warning so the scheduler always runs.

GYM_BUSYNESS has no public API — mock data is always used for that key.
"""

import warnings
from dotenv import load_dotenv
load_dotenv()

from scheduler.data.mock_week import (
    HARD_EVENTS,
    WEATHER,
    SLEEP_SCORES,
    GYM_BUSYNESS,
)


def build_week_context() -> dict:
    context = {
        "hard_events": _load_hard_events(),
        "weather":     _load_weather(),
        "sleep_scores": _load_sleep_scores(),
        "gym_busyness": GYM_BUSYNESS,   # no public API; always mock
    }
    return context


def _load_hard_events() -> list[dict]:
    try:
        from scheduler.data.providers.calendar_provider import fetch_hard_events
        events = fetch_hard_events()
        print("[week_context] Google Calendar: loaded live hard events")
        return events
    except BaseException as exc:
        warnings.warn(f"[week_context] Google Calendar unavailable ({exc}); using mock hard events")
        return HARD_EVENTS


def _load_weather() -> dict:
    try:
        from scheduler.data.providers.weather_provider import fetch_weather
        weather = fetch_weather()
        print("[week_context] OpenWeatherMap: loaded live weather")
        return weather
    except BaseException as exc:
        warnings.warn(f"[week_context] OpenWeatherMap unavailable ({exc}); using mock weather")
        return WEATHER


def _load_sleep_scores() -> dict:
    try:
        from scheduler.data.providers.sleep_provider import fetch_sleep_scores
        scores = fetch_sleep_scores()
        print("[week_context] Oura Ring: loaded live sleep scores")
        return scores
    except BaseException as exc:
        warnings.warn(f"[week_context] Oura Ring unavailable ({exc}); using mock sleep scores")
        return SLEEP_SCORES
