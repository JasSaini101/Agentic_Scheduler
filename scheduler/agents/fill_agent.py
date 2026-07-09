"""
Agent 4: Fill Agent
────────────────────
Scans each day for free gaps after all other agents have placed their entries,
then fills them with activities from the user's fill targets.

Two placement modes:
  - session-based: activity["sessions_per_week"] sessions of activity["duration"] hours each
    (e.g. "meal prep 2x/week for 1.5h" → placed up to 2 times across the week)
  - quota-based: a total hour budget split into chunks across the week
    (e.g. "20h personal project" → broken into MAX_CHUNK_HOURS slices until budget exhausted)

Rules:
  - Only fills gaps between fill_window_start and fill_window_end (from user_config or defaults)
  - Gaps shorter than MIN_GAP_HOURS are skipped
  - Quota activities are placed in as many slots as needed to exhaust the budget
  - Session activities are placed at most sessions_per_week times per week
"""

from scheduler.models import ScheduleEntry
from scheduler.data.mock_week import DAYS, FILL_ACTIVITIES

FILL_WINDOW_START = 8
FILL_WINDOW_END   = 22
MIN_GAP_HOURS     = 1.0
MAX_CHUNK_HOURS   = 4.0   # max single block for quota-based activities


def _free_gaps(day: str, blocked: list[dict], window_start: float, window_end: float) -> list[tuple[float, float]]:
    """Return list of (gap_start, gap_end) for the day within the fill window."""
    day_blocks = sorted(
        [b for b in blocked if b["day"] == day],
        key=lambda b: b["start_hour"],
    )

    gaps: list[tuple[float, float]] = []
    cursor = float(window_start)

    for block in day_blocks:
        b_start = max(block["start_hour"], window_start)
        b_end   = min(block["end_hour"],   window_end)

        if b_start > cursor:
            gaps.append((cursor, b_start))
        cursor = max(cursor, b_end)

    if cursor < window_end:
        gaps.append((cursor, window_end))

    return [(s, e) for s, e in gaps if e - s >= MIN_GAP_HOURS]


def _is_quota_based(activity: dict) -> bool:
    """Quota-based activities have a large total_hours that needs to be chunked."""
    return "total_hours" in activity


def run(week_context: dict, user_config=None) -> list[ScheduleEntry]:
    """
    Place fill activities into available gaps.
    week_context["blocked"] is the cumulative blocked-ranges list.
    """
    blocked = week_context.get("blocked", [])

    if user_config is not None:
        window_start, window_end = user_config.fill_window
        fill_targets = user_config.by_category("fill")
        if fill_targets:
            activities = []
            for t in fill_targets:
                total = t.sessions_per_week * t.duration_hours
                chunk = min(t.duration_hours, MAX_CHUNK_HOURS)
                activities.append({
                    "label": t.name.title(),
                    "total_hours": total,
                    "chunk_hours": chunk,
                    "reason": f"Weekly goal: {total:.0f}h total",
                })
        else:
            activities = list(FILL_ACTIVITIES)
    else:
        window_start, window_end = FILL_WINDOW_START, FILL_WINDOW_END
        activities = list(FILL_ACTIVITIES)

    entries: list[ScheduleEntry] = []

    # Track remaining budget or sessions for each activity
    # quota-based: remaining_hours[label] = hours left to place
    # session-based: sessions_left[label] = sessions left to place
    remaining: dict[str, float] = {}
    for act in activities:
        if _is_quota_based(act):
            remaining[act["label"]] = act["total_hours"]
        else:
            remaining[act["label"]] = float(act.get("sessions_per_week", 1))

    def _place(day: str, start: float, duration: float, activity: dict) -> None:
        nonlocal blocked
        end = start + duration
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
        blocked = blocked + [{"day": day, "start_hour": start, "end_hour": end}]

    # Round-robin placement: sweep the week repeatedly, placing at most ONE
    # chunk/session per activity per day per pass. This spreads work evenly
    # across the week instead of piling everything into Monday's first gaps.
    progress = True
    while progress and any(v > 0 for v in remaining.values()):
        progress = False
        for day in DAYS:
            for activity in activities:
                label = activity["label"]
                if remaining.get(label, 0) <= 0:
                    continue

                if _is_quota_based(activity):
                    place_hours = min(activity["chunk_hours"], remaining[label])
                    if place_hours < MIN_GAP_HOURS:
                        # Leftover sliver too small to schedule meaningfully
                        remaining[label] = 0
                        continue
                    needed = place_hours
                    duration = place_hours
                else:
                    duration = activity["duration"]
                    needed = duration + activity.get("padding", 0)

                gaps = _free_gaps(day, blocked, window_start, window_end)
                slot = next(((s, e) for s, e in gaps if e - s >= needed), None)
                if slot is None:
                    continue

                _place(day, slot[0], duration, activity)
                remaining[label] -= duration if _is_quota_based(activity) else 1
                progress = True

    # Warn about unplaced quota hours
    for act in activities:
        label = act["label"]
        left = remaining.get(label, 0)
        if _is_quota_based(act) and left > 0.5:
            import warnings
            warnings.warn(f"[fill_agent] Could only place {act['total_hours'] - left:.1f}/{act['total_hours']:.0f}h of '{label}' — not enough free time in week")

    return entries
