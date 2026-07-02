"""
Agent 3: Sport Agent
─────────────────────
Scores candidate outdoor sport slots across the week on two metrics,
then returns the top slots per sport type (one slot per day, shared seen_days guard).

Scoring breakdown (all normalized 0→1):
  - weather_score  : clear/warm = 1.0, rain = low, storm = rejected   weight: 0.55
  - hour_score     : morning slots preferred over late afternoon        weight: 0.45
"""

from scheduler.models import TimeSlot, ScheduleEntry
from scheduler.data.mock_week import (
    WEATHER,
    SPORT_CANDIDATE_HOURS,
    DAYS,
)

_DEFAULT_SPORT_DURATION = 1.5
_DEFAULT_TOP_N = 2
_DEFAULT_SPORT_LABEL = "Outdoor sport"

WEIGHTS = {
    "weather": 0.55,
    "hour":    0.45,
}

MORNING_HOURS = [8, 9, 10]

CONDITION_SCORE = {
    "clear":  1.0,
    "cloudy": 0.6,
    "rain":   0.2,
    "storm":  -1,
}

TEMP_IDEAL_LOW  = 65
TEMP_IDEAL_HIGH = 80
TEMP_MIN        = 45
TEMP_MAX        = 100


def _weather_score(day: str) -> tuple[float, str]:
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
    if hour in MORNING_HOURS:
        return 1.0, f"Preferred time: morning slot ({hour}:00)"
    score = max(0.0, 1.0 - (hour - 15) * 0.35)
    return round(score, 3), f"Preferred time: afternoon slot ({hour}:00)"


def _is_blocked(day: str, hour: int, blocked_ranges: list[dict], duration: float) -> bool:
    end = hour + duration
    for block in blocked_ranges:
        if block["day"] == day:
            if not (end <= block["start_hour"] or hour >= block["end_hour"]):
                return True
    return False


def _score_slots(
    duration: float,
    candidate_hours: list[int],
    blocked: list[dict],
) -> list[TimeSlot]:
    slots: list[TimeSlot] = []
    for day in DAYS:
        w_score, w_reason = _weather_score(day)
        if w_score < 0:
            continue
        for hour in candidate_hours:
            if _is_blocked(day, hour, blocked, duration):
                continue
            h_score, h_reason = _hour_score(hour)
            total = round(WEIGHTS["weather"] * w_score + WEIGHTS["hour"] * h_score, 3)
            slots.append(TimeSlot(
                day=day,
                start_hour=hour,
                duration_hours=duration,
                activity="",  # filled by caller
                score=total,
                reasons=[w_reason, h_reason],
            ))
    slots.sort(key=lambda s: s.score, reverse=True)
    return slots


def run(week_context: dict, user_config=None) -> list[ScheduleEntry]:
    """
    Score all candidate sport slots per sport type, return entries.
    seen_days is shared across all sport types — max one outdoor sport slot per day.
    """
    blocked = week_context.get("blocked", [])
    entries: list[ScheduleEntry] = []
    seen_days: set[str] = set()

    if user_config is not None:
        sport_targets = user_config.by_category("sport")
    else:
        sport_targets = []

    if not sport_targets:
        # Legacy fallback: no user_config, use defaults
        slots = _score_slots(_DEFAULT_SPORT_DURATION, SPORT_CANDIDATE_HOURS, blocked)
        for slot in slots:
            if len(entries) >= _DEFAULT_TOP_N:
                break
            if slot.day in seen_days:
                continue
            entry = ScheduleEntry(
                day=slot.day,
                start_hour=slot.start_hour,
                end_hour=slot.end_hour,
                label=_DEFAULT_SPORT_LABEL,
                category="sport",
                score=slot.score,
                reasons=slot.reasons,
            )
            print(f"  [sport_agent] Selected: {entry}")
            entries.append(entry)
            seen_days.add(slot.day)
        return entries

    for target in sport_targets:
        candidate_hours = target.candidate_hours if target.candidate_hours else SPORT_CANDIDATE_HOURS
        slots = _score_slots(target.duration_hours, candidate_hours, blocked)

        placed = 0
        for slot in slots:
            if placed >= target.sessions_per_week:
                break
            if slot.day in seen_days:
                continue
            slot.activity = target.name.title()
            entry = ScheduleEntry(
                day=slot.day,
                start_hour=slot.start_hour,
                end_hour=slot.end_hour,
                label=slot.activity,
                category="sport",
                score=slot.score,
                reasons=slot.reasons,
            )
            print(f"  [sport_agent] Selected ({target.name}): {entry}")
            entries.append(entry)
            seen_days.add(slot.day)
            # Add to blocked so later sports don't overlap
            blocked = blocked + [{"day": entry.day, "start_hour": entry.start_hour, "end_hour": entry.end_hour}]
            placed += 1

    return entries
