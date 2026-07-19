"""
Sleep Provider — Oura Ring API v2
──────────────────────────────────
Env var required: OURA_ACCESS_TOKEN (Personal Access Token from Oura cloud)

Returns a dict matching the SLEEP_SCORES mock schema:
  {day: int}  (1–10 scale)

Oura reports a sleep score 0–100; we map it linearly to 1–10.
"""

import os
from datetime import timedelta

import requests

from scheduler.data.mock_week import DAYS, date_for_day


_API_BASE = "https://api.ouraring.com/v2/usercollection/daily_sleep"


def _oura_score_to_1_10(score: int) -> int:
    """Map Oura 0–100 score to 1–10 scale."""
    return max(1, min(10, round(score / 10)))


def fetch_sleep_scores() -> dict:
    """
    Returns {day_name: int (1–10)} for the current week.
    Raises RuntimeError if the token is missing or the call fails.
    """
    token = os.environ.get("OURA_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("OURA_ACCESS_TOKEN not set")

    from scheduler.data.mock_week import local_today
    week_start = local_today()
    week_end = week_start + timedelta(days=7)
    resp = requests.get(
        _API_BASE,
        headers={"Authorization": f"Bearer {token}"},
        params={
            "start_date": week_start.isoformat(),
            "end_date": week_end.isoformat(),
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    # Build date → score map from the response
    date_to_score: dict[str, int] = {}
    for record in data.get("data", []):
        date_str = record.get("day") or record.get("date", "")[:10]
        raw_score = record.get("score")
        if date_str and raw_score is not None:
            date_to_score[date_str] = _oura_score_to_1_10(raw_score)

    result = {}
    for day_name in DAYS:
        date_key = date_for_day(day_name).isoformat()
        if date_key in date_to_score:
            result[day_name] = date_to_score[date_key]

    return result
