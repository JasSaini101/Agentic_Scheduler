"""
Gym Busyness Provider — Google Popular Times
─────────────────────────────────────────────
Env vars required: GOOGLE_MAPS_API_KEY
                   PLANET_FITNESS_PLACE_ID

Returns a dict matching the GYM_BUSYNESS mock schema:
  {(day, hour): float}  where float is 0.0 (empty) → 1.0 (packed)

Popular Times returns a 0–100 scale per hour per day; we divide by 100.
Only hours present in GYM_CANDIDATE_HOURS are included.
"""

import os

from scheduler.data.mock_week import DAYS, GYM_CANDIDATE_HOURS


def fetch_gym_busyness() -> dict:
    """
    Returns {(day, hour): float} for all candidate gym hours.
    Raises RuntimeError on missing config or failed API call so
    callers can fall back to mock data.
    """
    api_key  = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    place_id = os.environ.get("PLANET_FITNESS_PLACE_ID", "").strip()

    if not api_key:
        raise RuntimeError("GOOGLE_MAPS_API_KEY not set")
    if not place_id:
        raise RuntimeError("PLANET_FITNESS_PLACE_ID not set")

    try:
        import populartimes
    except ImportError:
        raise RuntimeError(
            "populartimes not installed — run: pip install populartimes"
        )

    data = populartimes.get_id(api_key, place_id)

    popular = data.get("populartimes")
    if not popular:
        raise RuntimeError(
            "No populartimes data returned — place may not have enough visits yet"
        )

    # populartimes returns a list of 7 dicts ordered Mon–Sun:
    # [{"name": "Monday", "data": [0, 0, ..., 45, 70, ...]}, ...]
    # Each "data" list has 24 values (index = hour 0–23).
    result = {}
    for entry in popular:
        day_name = entry.get("name")
        hourly   = entry.get("data", [])
        if day_name not in DAYS or len(hourly) < 24:
            continue
        for hour in GYM_CANDIDATE_HOURS:
            raw = hourly[hour]           # 0–100
            result[(day_name, hour)] = round(raw / 100, 3)

    if not result:
        raise RuntimeError("populartimes data parsed but produced no usable slots")

    return result
