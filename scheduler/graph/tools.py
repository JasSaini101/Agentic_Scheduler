"""
Deterministic validators shared by the graph and the test suite.

Slot placement lives in the CP-SAT solver (scheduler/solver); the legacy
scoring agents in scheduler/agents feed the adjustment engine and default
preference weights.
"""

from scheduler.models import ScheduleEntry


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
