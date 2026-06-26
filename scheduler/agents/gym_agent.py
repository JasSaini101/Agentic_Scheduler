"""
Agent 2: Gym Agent
──────────────────
Scores every candidate gym slot across the week on three metrics,
then returns the top 3 non-conflicting slots.

Scoring breakdown (all normalized 0→1, equally weighted):
  - gym_busyness_score : 1.0 = empty gym, 0.0 = packed
  - rest_score         : based on sleep score for that day (1–10 → 0→1)
  - weather_score      : gym is indoors, but extreme heat/storm is a deterrent
                         for the commute. Small factor here.

Future upgrades:
  - gym_busyness → real Popular Times API
  - rest_score   → Oura Ring / Garmin API
  - weather      → OpenWeatherMap hourly
"""

from scheduler.models import TimeSlot, ScheduleEntry
from scheduler.data.mock_week import (
    WEATHER,
    GYM_BUSYNESS,
    SLEEP_SCORES,
    GYM_CANDIDATE_HOURS,
    DAYS,
)

# Duration of a gym session in hours
GYM_DURATION = 1.5

# How many slots to return
TOP_N = 3

# Scoring weights (must sum to 1.0)
WEIGHTS = {
    "busyness": 0.45,
    "rest":     0.40,
    "weather":  0.15,
}


def _busyness_score(day: str, hour: int) -> tuple[float, str]:
    """Lower busyness = higher score."""
    busyness = GYM_BUSYNESS.get((day, hour), 0.5)
    score = 1.0 - busyness
    level = "quiet" if busyness < 0.4 else "moderate" if busyness < 0.7 else "busy"
    return score, f"Gym is {level} ({int(busyness * 100)}% capacity)"


def _rest_score(day: str) -> tuple[float, str]:
    """Higher sleep score = higher rest score."""
    sleep = SLEEP_SCORES.get(day, 7)
    score = sleep / 10.0
    label = "well-rested" if sleep >= 8 else "average rest" if sleep >= 6 else "fatigued"
    return score, f"Body is {label} (sleep score {sleep}/10)"


def _weather_score(day: str) -> tuple[float, str]:
    """Gym is indoors; penalise only storms (miserable commute)."""
    condition = WEATHER.get(day, {}).get("condition", "clear")
    temp = WEATHER.get(day, {}).get("temp_f", 75)
    if condition == "storm":
        return 0.4, "Storm warning — commute is rough"
    if temp > 95:
        return 0.6, f"Very hot outside ({temp}°F)"
    return 1.0, f"Weather fine for commute ({condition}, {temp}°F)"


def _is_blocked(day: str, hour: int, blocked_ranges: list[dict]) -> bool:
    """Check if this slot overlaps any already-locked event."""
    end = hour + GYM_DURATION
    for block in blocked_ranges:
        if block["day"] == day:
            if not (end <= block["start_hour"] or hour >= block["end_hour"]):
                return True
    return False


def run(week_context: dict) -> list[ScheduleEntry]:
    """
    Score all candidate slots, filter blocked ones, return top TOP_N.
    week_context["blocked"] is a list of already-locked hour ranges.
    """
    blocked = week_context.get("blocked", [])
    slots: list[TimeSlot] = []

    for day in DAYS:
        for hour in GYM_CANDIDATE_HOURS:
            if _is_blocked(day, hour, blocked):
                continue

            b_score, b_reason = _busyness_score(day, hour)
            r_score, r_reason = _rest_score(day)
            w_score, w_reason = _weather_score(day)

            total = (
                WEIGHTS["busyness"] * b_score
                + WEIGHTS["rest"]     * r_score
                + WEIGHTS["weather"]  * w_score
            )

            slot = TimeSlot(
                day=day,
                start_hour=hour,
                duration_hours=GYM_DURATION,
                activity="Gym session",
                score=round(total, 3),
                reasons=[b_reason, r_reason, w_reason],
            )
            slots.append(slot)

    # Sort by score descending, pick top N
    slots.sort(key=lambda s: s.score, reverse=True)
    top_slots = slots[:TOP_N]

    entries = []
    for slot in top_slots:
        entry = ScheduleEntry(
            day=slot.day,
            start_hour=slot.start_hour,
            end_hour=slot.end_hour,
            label=slot.activity,
            category="gym",
            score=slot.score,
            reasons=slot.reasons,
        )
        print(f"  [gym_agent] Selected: {entry}")
        for r in slot.reasons:
            print(f"              → {r}")
        entries.append(entry)

    return entries
