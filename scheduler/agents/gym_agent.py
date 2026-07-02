"""
Agent 2: Gym Agent
──────────────────
Scores every candidate gym slot across the week on three metrics,
then returns the top 3 non-conflicting slots.

Scoring breakdown (all normalized 0→1):
  - busyness_score     : 1.0 = empty gym, 0.0 = packed           weight: 0.35
  - time_available     : 1.0 = 2.5+ free hours after gym,        weight: 0.45
                         0.0 = less than 1h (slot rejected)
  - rest_score         : sleep score mapped from 1–10 scale       weight: 0.20

Deferred factors:
  - weather_score      : storm/heat penalty for commute

Future upgrades:
  - gym_busyness → real Popular Times API
"""

from scheduler.models import TimeSlot, ScheduleEntry
from scheduler.data.mock_week import (
    GYM_BUSYNESS,
    GYM_CANDIDATE_HOURS,
    SLEEP_SCORES,
    DAYS,
)

_DEFAULT_GYM_DURATION = 1.5
_DEFAULT_TOP_N = 3

WEIGHTS = {
    "busyness":       0.35,
    "time_available": 0.45,
    "rest":           0.20,
}

# Free-time thresholds (hours of buffer after gym ends)
MIN_BUFFER   = 1.0   # below this → slot is rejected outright
IDEAL_BUFFER = 2.5   # at or above this → full score of 1.0


def _rest_score(day: str, sleep_scores: dict) -> tuple[float, str]:
    """Normalize a 1–10 sleep score to 0→1. Missing data defaults to 0.5."""
    raw = sleep_scores.get(day, 5)
    score = round((raw - 1) / 9, 3)
    level = "well-rested" if raw >= 7 else "moderate rest" if raw >= 5 else "fatigued"
    return score, f"Sleep score: {raw}/10 ({level})"


def _busyness_score(day: str, hour: int) -> tuple[float, str]:
    """Lower busyness = higher score."""
    busyness = GYM_BUSYNESS.get((day, hour), 0.5)
    score = 1.0 - busyness
    level = "quiet" if busyness < 0.4 else "moderate" if busyness < 0.7 else "busy"
    return score, f"Gym is {level} ({int(busyness * 100)}% capacity)"


def _time_available_score(day: str, hour: int, blocked_ranges: list[dict], duration: float = _DEFAULT_GYM_DURATION) -> tuple[float, str]:
    """
    Score based on free time between gym-end and the next blocked event.
    Returns (score, reason). Returns (-1, reason) if buffer is below minimum
    so the caller can reject the slot entirely.
    """
    gym_end = hour + duration

    # Find the soonest event on this day that starts at or after gym_end
    next_start = None
    for block in blocked_ranges:
        if block["day"] == day and block["start_hour"] >= gym_end:
            if next_start is None or block["start_hour"] < next_start:
                next_start = block["start_hour"]

    if next_start is None:
        return 1.0, "Rest of day free after gym"

    buffer = next_start - gym_end

    if buffer < MIN_BUFFER:
        return -1, f"Only {buffer:.1f}h before next event — not enough recovery time"

    # Linear scale: MIN_BUFFER → 0.0, IDEAL_BUFFER → 1.0
    score = min((buffer - MIN_BUFFER) / (IDEAL_BUFFER - MIN_BUFFER), 1.0)
    return round(score, 3), f"{buffer:.1f}h free after gym before next event"


def _is_blocked(day: str, hour: int, blocked_ranges: list[dict], duration: float = _DEFAULT_GYM_DURATION) -> bool:
    """Check if this slot overlaps any already-locked event."""
    end = hour + duration
    for block in blocked_ranges:
        if block["day"] == day:
            if not (end <= block["start_hour"] or hour >= block["end_hour"]):
                return True
    return False


def run(week_context: dict, user_config=None) -> list[ScheduleEntry]:
    """
    Score all candidate slots, filter blocked/tight ones, return top N.
    week_context["blocked"] is a list of already-locked hour ranges.
    week_context["sleep_scores"] is an optional {day: int} map (1–10 scale).
    """
    blocked = week_context.get("blocked", [])
    sleep_scores = week_context.get("sleep_scores", SLEEP_SCORES)

    if user_config is not None:
        gym_targets = user_config.by_category("gym")
        gym_target = gym_targets[0] if gym_targets else None
    else:
        gym_target = None

    gym_duration = gym_target.duration_hours if gym_target else _DEFAULT_GYM_DURATION
    top_n = gym_target.sessions_per_week if gym_target else _DEFAULT_TOP_N
    candidate_hours = gym_target.candidate_hours if gym_target else GYM_CANDIDATE_HOURS

    slots: list[TimeSlot] = []

    for day in DAYS:
        for hour in candidate_hours:
            if _is_blocked(day, hour, blocked, gym_duration):
                continue

            b_score, b_reason = _busyness_score(day, hour)
            t_score, t_reason = _time_available_score(day, hour, blocked, gym_duration)
            r_score, r_reason = _rest_score(day, sleep_scores)

            if t_score < 0:
                print(f"  [gym_agent] Skipped {day} {hour}:00 → {t_reason}")
                continue

            total = (
                WEIGHTS["busyness"]       * b_score
                + WEIGHTS["time_available"] * t_score
                + WEIGHTS["rest"]           * r_score
            )

            slot = TimeSlot(
                day=day,
                start_hour=hour,
                duration_hours=gym_duration,
                activity="Gym session",
                score=round(total, 3),
                reasons=[b_reason, t_reason, r_reason],
            )
            slots.append(slot)

    slots.sort(key=lambda s: s.score, reverse=True)

    entries = []
    seen_days: set[str] = set()
    for slot in slots:
        if len(entries) >= top_n:
            break
        if slot.day in seen_days:
            continue
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
        seen_days.add(slot.day)

    return entries