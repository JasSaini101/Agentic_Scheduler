"""
Agent 4: Fill Agent
────────────────────
Scans each day for free gaps after all other agents have placed their entries,
then fills them with secondary activities from a priority-ordered list.

Rules:
  - Only fills gaps between FILL_WINDOW_START and FILL_WINDOW_END
  - Each activity has a minimum gap requirement (duration + padding)
  - Each activity type is placed at most once per week
  - Gaps shorter than MIN_GAP_HOURS are left empty

Future upgrades:
  - Activity list → user preferences / energy model
  - Padding → commute time from Google Maps
"""

from scheduler.models import ScheduleEntry
from scheduler.data.mock_week import DAYS, FILL_ACTIVITIES

FILL_WINDOW_START = 8    # earliest hour to place fill activities
FILL_WINDOW_END   = 22   # latest end hour allowed
MIN_GAP_HOURS     = 1.0  # ignore gaps smaller than this


def _free_gaps(day: str, blocked: list[dict]) -> list[tuple[float, float]]:
    """Return list of (gap_start, gap_end) for the day within the fill window."""
    day_blocks = sorted(
        [b for b in blocked if b["day"] == day],
        key=lambda b: b["start_hour"],
    )

    gaps: list[tuple[float, float]] = []
    cursor = float(FILL_WINDOW_START)

    for block in day_blocks:
        b_start = max(block["start_hour"], FILL_WINDOW_START)
        b_end   = min(block["end_hour"],   FILL_WINDOW_END)

        if b_start > cursor:
            gaps.append((cursor, b_start))
        cursor = max(cursor, b_end)

    if cursor < FILL_WINDOW_END:
        gaps.append((cursor, FILL_WINDOW_END))

    return [(s, e) for s, e in gaps if e - s >= MIN_GAP_HOURS]


def run(week_context: dict, user_config=None) -> list[ScheduleEntry]:
    """
    Place fill activities into the first available gap that fits each one.
    week_context["blocked"] is the cumulative blocked-ranges list.
    """
    blocked = week_context.get("blocked", [])

    if user_config is not None:
        fill_targets = user_config.by_category("fill")
        if fill_targets:
            # Convert ActivityTarget list to the same format as FILL_ACTIVITIES
            activities = [
                {"label": t.name.title(), "duration": t.duration_hours, "padding": 0.5, "reason": f"Goal: {t.sessions_per_week}x per week"}
                for t in fill_targets
            ]
        else:
            activities = list(FILL_ACTIVITIES)
    else:
        activities = list(FILL_ACTIVITIES)

    entries: list[ScheduleEntry] = []
    placed_labels: set[str] = set()

    for day in DAYS:
        gaps = _free_gaps(day, blocked)

        for gap_start, gap_end in gaps:
            gap_size = gap_end - gap_start
            for activity in activities:
                if activity["label"] in placed_labels:
                    continue
                needed = activity["duration"] + activity.get("padding", 0)
                if gap_size < needed:
                    continue

                start = gap_start
                end   = start + activity["duration"]
                entry = ScheduleEntry(
                    day=day,
                    start_hour=int(start),
                    end_hour=end,
                    label=activity["label"],
                    category="fill",
                    reasons=[activity.get("reason", "")],
                )
                print(f"  [fill_agent]  Placed:   {entry}")
                entries.append(entry)
                placed_labels.add(activity["label"])

                # Update blocked so subsequent activities on the same day
                # don't overlap this newly placed one
                blocked = blocked + [{"day": day, "start_hour": start, "end_hour": end}]
                break   # move to next gap after placing one activity

    return entries
