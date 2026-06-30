"""
Agent 2: Gym Agent
──────────────────
Scores every candidate gym slot across the week on two metrics,
then returns the top 3 non-conflicting slots.

Scoring breakdown (all normalized 0→1):
  - busyness_score     : 1.0 = empty gym, 0.0 = packed           weight: 0.45
  - time_available     : 1.0 = 2.5+ free hours after gym,        weight: 0.55
                         0.0 = less than 1h (slot rejected)

Deferred factors (add when data sources are ready):
  - rest_score         : sleep score from Oura / Garmin / manual input
  - weather_score      : storm/heat penalty for commute

Future upgrades:
  - gym_busyness → real Popular Times API
"""

from scheduler.models import TimeSlot, ScheduleEntry
from scheduler.data.mock_week import (
    GYM_BUSYNESS,
    GYM_CANDIDATE_HOURS,
    DAYS,
)

GYM_DURATION = 1.5   # hours
TOP_N = 3

WEIGHTS = {
    "busyness":       0.45,
    "time_available": 0.55,
}

# Free-time thresholds (hours of buffer after gym ends)
MIN_BUFFER   = 1.0   # below this → slot is rejected outright
IDEAL_BUFFER = 2.5   # at or above this → full score of 1.0


def _busyness_score(day: str, hour: int) -> tuple[float, str]:
    """Lower busyness = higher score."""
    busyness = GYM_BUSYNESS.get((day, hour), 0.5)
    score = 1.0 - busyness
    level = "quiet" if busyness < 0.4 else "moderate" if busyness < 0.7 else "busy"
    return score, f"Gym is {level} ({int(busyness * 100)}% capacity)"


def _time_available_score(day: str, hour: int, blocked_ranges: list[dict]) -> tuple[float, str]:
    """
    Score based on free time between gym-end and the next blocked event.
    Returns (score, reason). Returns (-1, reason) if buffer is below minimum
    so the caller can reject the slot entirely.
    """
    gym_end = hour + GYM_DURATION

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
    Score all candidate slots, filter blocked/tight ones, return top TOP_N.
    week_context["blocked"] is a list of already-locked hour ranges.
    """
    blocked = week_context.get("blocked", [])
    slots: list[TimeSlot] = []

    for day in DAYS:
        for hour in GYM_CANDIDATE_HOURS:
            if _is_blocked(day, hour, blocked):
                continue

            b_score, b_reason = _busyness_score(day, hour)
            t_score, t_reason = _time_available_score(day, hour, blocked)

            if t_score < 0:
                print(f"  [gym_agent] Skipped {day} {hour}:00 → {t_reason}")
                continue

            total = (
                WEIGHTS["busyness"]       * b_score
                + WEIGHTS["time_available"] * t_score
            )

            slot = TimeSlot(
                day=day,
                start_hour=hour,
                duration_hours=GYM_DURATION,
                activity="Gym session",
                score=round(total, 3),
                reasons=[b_reason, t_reason],
            )
            slots.append(slot)

    slots.sort(key=lambda s: s.score, reverse=True)

    entries = []
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