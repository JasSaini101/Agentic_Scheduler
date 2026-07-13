"""
Deterministic scheduling tools.

The scoring/packing engines stay rule-based and testable; LLM agents CHOOSE
among their outputs rather than inventing times.
"""

from scheduler.config import UserConfig
from scheduler.models import ScheduleEntry, TimeSlot
import scheduler.agents.gym_agent as gym_agent
import scheduler.agents.sport_agent as sport_agent
import scheduler.agents.fill_agent as fill_agent
import scheduler.agents.hard_block_agent as hard_block_agent


def blocked_ranges(entries: list[ScheduleEntry]) -> list[dict]:
    return [
        {"day": e.day, "start_hour": e.start_hour, "end_hour": e.end_hour}
        for e in entries
    ]


def gym_candidates(week_context: dict, entries: list[ScheduleEntry],
                   user_config: UserConfig | None, limit: int = 10) -> list[TimeSlot]:
    """Top scored gym slots given everything already on the schedule."""
    ctx = {**week_context, "blocked": blocked_ranges(entries)}
    return gym_agent.score_candidates(ctx, user_config)[:limit]


def sport_candidates(week_context: dict, entries: list[ScheduleEntry],
                     target, limit: int = 10) -> list[TimeSlot]:
    """Top scored outdoor-sport slots for one sport target."""
    from scheduler.data.mock_week import DAYS, SPORT_CANDIDATE_HOURS
    blocked = blocked_ranges(entries)
    hours = target.candidate_hours or SPORT_CANDIDATE_HOURS
    days = [target.day] if target.day else DAYS
    return sport_agent._score_slots(
        target.duration_hours, hours, blocked, days, pinned=target.day is not None
    )[:limit]


def hard_blocks(week_context: dict) -> list[ScheduleEntry]:
    """Immovable events already on the user's calendar."""
    return hard_block_agent.run(week_context)


def fill_plan(week_context: dict, entries: list[ScheduleEntry],
              user_config: UserConfig | None) -> list[ScheduleEntry]:
    """Deterministic round-robin packing of fill activities into free gaps."""
    ctx = {**week_context, "blocked": blocked_ranges(entries)}
    return fill_agent.run(ctx, user_config)


def find_conflicts(entries: list[ScheduleEntry]) -> list[str]:
    """Hard validator: overlapping entries on the same day.

    Overlaps between two hard_block entries are ignored — those are the
    user's own pre-existing calendar events (e.g. a 1:1 nested inside a
    Work block) and not something the scheduler created."""
    conflicts = []
    by_day: dict[str, list[ScheduleEntry]] = {}
    for e in entries:
        by_day.setdefault(e.day, []).append(e)
    for day, day_entries in by_day.items():
        day_entries.sort(key=lambda e: (e.start_hour, e.end_hour))
        for i, a in enumerate(day_entries):
            for b in day_entries[i + 1:]:
                if b.start_hour >= a.end_hour:
                    break
                if a.category == "hard_block" and b.category == "hard_block":
                    continue
                conflicts.append(
                    f"{day}: '{a.label}' ({a.start_hour}–{a.end_hour}) overlaps "
                    f"'{b.label}' ({b.start_hour}–{b.end_hour})"
                )
    return conflicts
