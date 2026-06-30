"""
Orchestrator
─────────────
Coordinates all agents. Runs them in dependency order:
  1. Hard block agent first  (sets the immovable constraints)
  2. Gym agent               (avoids hard blocks)
  3. [Sport agent]           (Phase 1 stub)
  4. [Fill agent]            (Phase 1 stub)

Then merges all outputs into a single weekly schedule and prints it.
"""

from collections import defaultdict
from scheduler.models import ScheduleEntry
from scheduler.data.mock_week import DAYS
from scheduler.week_context import build_week_context
import scheduler.agents.hard_block_agent as hard_block_agent
import scheduler.agents.gym_agent as gym_agent
import scheduler.agents.sport_agent as sport_agent
import scheduler.agents.fill_agent as fill_agent


def _build_blocked_ranges(entries: list[ScheduleEntry]) -> list[dict]:
    """Convert confirmed entries into a list of blocked hour ranges for agents to check."""
    return [
        {"day": e.day, "start_hour": e.start_hour, "end_hour": e.end_hour}
        for e in entries
    ]


def _print_schedule(schedule: list[ScheduleEntry]) -> None:
    by_day = defaultdict(list)
    for entry in schedule:
        by_day[entry.day].append(entry)

    print("\n" + "═" * 52)
    print("  WEEKLY SCHEDULE")
    print("═" * 52)

    for day in DAYS:
        entries = sorted(by_day.get(day, []), key=lambda e: e.start_hour)
        if not entries:
            print(f"\n  {day}")
            print("    — No plans yet")
            continue
        print(f"\n  {day}")
        for entry in entries:
            cat_badge = {
                "hard_block": "🔒",
                "gym":        "🏋️",
                "sport":      "🎾",
                "fill":       "✨",
            }.get(entry.category, "•")
            score_str = f"  (score: {entry.score:.2f})" if entry.score else ""
            start = entry.start_hour
            end = int(entry.end_hour)
            start_fmt = f"{start % 12 or 12}{'am' if start < 12 else 'pm'}"
            end_fmt   = f"{end   % 12 or 12}{'am' if end   < 12 else 'pm'}"
            print(f"    {cat_badge}  {start_fmt}–{end_fmt:6s} {entry.label}{score_str}")

    print("\n" + "═" * 52)


def run() -> list[ScheduleEntry]:
    all_entries: list[ScheduleEntry] = []

    week_context = build_week_context()

    # ── Step 1: Hard blocks ───────────────────────────────────────────────────
    print("\n[orchestrator] Running hard_block_agent...")
    hard_entries = hard_block_agent.run(week_context)
    all_entries.extend(hard_entries)

    # ── Step 2: Gym agent (aware of hard blocks) ──────────────────────────────
    print("\n[orchestrator] Running gym_agent...")
    gym_entries = gym_agent.run({**week_context, "blocked": _build_blocked_ranges(all_entries)})
    all_entries.extend(gym_entries)

    # ── Step 3: Sport agent (avoids hard blocks + gym) ───────────────────────
    print("\n[orchestrator] Running sport_agent...")
    sport_entries = sport_agent.run({**week_context, "blocked": _build_blocked_ranges(all_entries)})
    all_entries.extend(sport_entries)

    # ── Step 4: Fill agent (fills remaining gaps last) ────────────────────────
    print("\n[orchestrator] Running fill_agent...")
    fill_entries = fill_agent.run({**week_context, "blocked": _build_blocked_ranges(all_entries)})
    all_entries.extend(fill_entries)

    # ── Print final schedule ──────────────────────────────────────────────────
    _print_schedule(all_entries)

    return all_entries


if __name__ == "__main__":
    run()
