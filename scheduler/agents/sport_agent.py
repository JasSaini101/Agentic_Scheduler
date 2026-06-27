"""
Agent 3: Sport Agent
─────────────────────
Scores candidate outdoor sport slots across the week on two metrics,
then returns the top 2 non-conflicting slots.

Scoring breakdown (all normalized 0→1):
  - weather_score  : clear/warm = 1.0, rain = low, storm = rejected   weight: 0.55
  - hour_score     : morning slots preferred over late afternoon        weight: 0.45

Supported sport types: tennis, basketball (outdoor)

Future upgrades:
  - weather → real OpenWeatherMap hourly forecast
  - sport preference → user config (preferred sport, skill level)
"""

from scheduler.models import TimeSlot, ScheduleEntry
from scheduler.data.mock_week import (
    WEATHER,
    SPORT_CANDIDATE_HOURS,
    DAYS,
)

SPORT_DURATION = 1.5   # hours
TOP_N = 2

WEIGHTS = {
    "weather": 0.55,
    "hour":    0.45,
}

# Preferred morning hours (scores 1.0); late afternoon scores decay linearly
MORNING_HOURS = [8, 9, 10]
AFTERNOON_HOURS = [16, 17]

# Weather condition scores
CONDITION_SCORE = {
    "clear":  1.0,
    "cloudy": 0.6,
    "rain":   0.2,
    "storm":  -1,   # rejected outright
}

# Temperature sweet spot: 65–80°F scores 1.0; outside that range decays
TEMP_IDEAL_LOW  = 65
TEMP_IDEAL_HIGH = 80
TEMP_MIN        = 45
TEMP_MAX        = 100


def _weather_score(day: str) -> tuple[float, str]:
    """Score the day's weather for outdoor sport."""
    w = WEATHER.get(day, {"condition": "clear", "temp_f": 72})
    condition = w["condition"]
    temp = w["temp_f"]

    cond_score = CONDITION_SCORE.get(condition, 0.5)
    if cond_score < 0:
        return -1, f"Weather: {condition} — unsafe for outdoor sport"

    if TEMP_IDEAL_LOW <= temp <= TEMP_IDEAL_HIGH:
        temp_score = 1.0
    elif temp < TEMP_IDEAL_LOW:
        temp_score = max(0.0, (temp - TEMP_MIN) / (TEMP_IDEAL_LOW - TEMP_MIN))
    else:
        temp_score = max(0.0, 1.0 - (temp - TEMP_IDEAL_HIGH) / (TEMP_MAX - TEMP_IDEAL_HIGH))

    combined = round((cond_score + temp_score) / 2, 3)
    return combined, f"Weather: {condition}, {temp}°F"


def _hour_score(hour: int) -> tuple[float, str]:
    """Morning slots score higher; afternoon decays."""
    if hour in MORNING_HOURS:
        score = 1.0
        label = "morning slot"
    else:
        # 16 → 0.5, 17 → 0.3
        score = max(0.0, 1.0 - (hour - 15) * 0.35)
        label = "afternoon slot"
    return round(score, 3), f"Preferred time: {label} ({hour}:00)"


def _is_blocked(day: str, hour: int, blocked_ranges: list[dict]) -> bool:
    end = hour + SPORT_DURATION
    for block in blocked_ranges:
        if block["day"] == day:
            if not (end <= block["start_hour"] or hour >= block["end_hour"]):
                return True
    return False


def run(week_context: dict) -> list[ScheduleEntry]:
    """
    Score all candidate sport slots, filter blocked/storm ones, return top TOP_N.
    week_context["blocked"] is the cumulative list of locked hour ranges.
    """
    blocked = week_context.get("blocked", [])
    slots: list[TimeSlot] = []

    for day in DAYS:
        w_score, w_reason = _weather_score(day)
        if w_score < 0:
            print(f"  [sport_agent] Skipped all {day} slots → {w_reason}")
            continue

        for hour in SPORT_CANDIDATE_HOURS:
            if _is_blocked(day, hour, blocked):
                continue

            h_score, h_reason = _hour_score(hour)
            total = round(
                WEIGHTS["weather"] * w_score + WEIGHTS["hour"] * h_score, 3
            )

            slot = TimeSlot(
                day=day,
                start_hour=hour,
                duration_hours=SPORT_DURATION,
                activity="Outdoor sport (tennis / basketball)",
                score=total,
                reasons=[w_reason, h_reason],
            )
            slots.append(slot)

    slots.sort(key=lambda s: s.score, reverse=True)

    entries = []
    # Pick top TOP_N, but ensure no two slots on the same day
    seen_days: set[str] = set()
    for slot in slots:
        if len(entries) >= TOP_N:
            break
        if slot.day in seen_days:
            continue
        entry = ScheduleEntry(
            day=slot.day,
            start_hour=slot.start_hour,
            end_hour=slot.end_hour,
            label=slot.activity,
            category="sport",
            score=slot.score,
            reasons=slot.reasons,
        )
        print(f"  [sport_agent] Selected: {entry}")
        for r in slot.reasons:
            print(f"               → {r}")
        entries.append(entry)
        seen_days.add(slot.day)

    return entries
