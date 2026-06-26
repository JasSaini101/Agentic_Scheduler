"""
Agent 1: Hard Block Agent
─────────────────────────
Reads the hard events list and converts them into confirmed ScheduleEntries.
These are immovable — no scoring, no negotiation.

Future upgrade: replace HARD_EVENTS import with a Google Calendar API call.
"""

from scheduler.models import ScheduleEntry
from scheduler.data.mock_week import HARD_EVENTS


def run(week_context: dict) -> list[ScheduleEntry]:
    """
    Returns a list of locked ScheduleEntries for the week.

    week_context is passed in by the orchestrator and may contain
    overrides or extra events added at runtime (e.g. a new calendar invite).
    For now we just use the mock data.
    """
    hard_events = week_context.get("hard_events", HARD_EVENTS)
    entries = []

    for event in hard_events:
        entry = ScheduleEntry(
            day=event["day"],
            start_hour=event["start_hour"],
            end_hour=event["end_hour"],
            label=event["label"],
            category="hard_block",
        )
        entries.append(entry)
        print(f"  [hard_block] Locked: {entry}")

    return entries
