"""
User change requests → ConstraintPatch → re-solve.

The web adjust flow uses the same mechanism as the critic's revision loop:
the LLM never edits the schedule — it edits the constraint set, and CP-SAT
re-solves the whole week. That is what lets requests like "add two gym
sessions at 12 or later" or "tennis on back to back days" come out
conflict-free and globally placed.
"""

import logging

from scheduler.data.mock_week import DAYS
from scheduler.models import ScheduleEntry
from scheduler.solver import ConstraintPatch, ConstraintSet, solve

logger = logging.getLogger("agentic_scheduler.graph")


def _summarize_requests(cs: ConstraintSet) -> str:
    lines = []
    for r in cs.requests:
        desc = f"- {r.name} ({r.category}): "
        desc += (f"{r.total_hours}h quota" if r.total_hours is not None
                 else f"{r.sessions} session(s) × {r.session_hours}h")
        if r.pinned_day:
            desc += f", pinned to {r.pinned_day}"
        if r.pinned_days:
            desc += f", sessions pinned to {r.pinned_days}"
        if r.allowed_hours:
            desc += f", start hours {r.allowed_hours}"
        lines.append(desc)
    return "\n".join(lines) or "(none)"


def make_change_patch(llm, change: str, cs: ConstraintSet) -> ConstraintPatch:
    """Ask the LLM to express the user's change as a ConstraintPatch."""
    prompt = (
        "You maintain the constraint set of a schedule solver. "
        f"Current activity requests:\n{_summarize_requests(cs)}\n\n"
        f"The user requested this change:\n{change}\n\n"
        "Emit a ConstraintPatch implementing it. Tools at your disposal:\n"
        "- update_requests: activity name → field overrides on its request. "
        "Fields: sessions (int), session_hours (float), total_hours (float), "
        "allowed_hours (list of int start hours, e.g. [12,13,14,15,16,17,18,19,20,21] "
        "for 'at 12 or later'), pinned_day (str), pinned_days (list of str — one day "
        "per session; use CONSECUTIVE days for 'back to back'), one_per_day (bool).\n"
        "- add_requests: brand-new activities (name, category gym|sport|fill, "
        "sessions, session_hours).\n"
        "- remove_activities: activity names to drop entirely.\n"
        "- pin_activity: activity name → single day (all sessions).\n"
        "- add_exclusions / add_preferences: block or nudge time ranges "
        "(preference weights -5..5).\n"
        f"Day names: {', '.join(DAYS)}. To ADD sessions of an existing activity, "
        "use update_requests with the new TOTAL session count, not add_requests. "
        "Smallest change that satisfies the request wins. "
        "Set rationale to one sentence explaining the edit."
    )
    return llm.with_structured_output(ConstraintPatch).invoke(prompt)


def apply_change(change: str, schedule: list[ScheduleEntry], cs: ConstraintSet,
                 llm=None, patch_fn=None):
    """Apply a natural-language change to a solved schedule.

    Returns (entries, updated_constraints, unplaced). Hard-block entries from
    the existing schedule are preserved; everything else is re-solved.
    patch_fn(change, cs) → ConstraintPatch is injectable for tests."""
    if patch_fn is None:
        if llm is None:
            raise ValueError("apply_change needs an LLM or an injectable patch_fn")
        patch_fn = lambda c, s: make_change_patch(llm, c, s)

    patch = patch_fn(change, cs)
    updated = patch.apply(cs)
    logger.info("[adjust] patch: %s", patch.rationale or patch.model_dump(exclude_defaults=True))

    result = solve(updated)
    hard = [e for e in schedule if e.category == "hard_block"]
    entries = hard + result.entries
    entries.sort(key=lambda e: (DAYS.index(e.day), e.start_hour))
    return entries, updated, result.unplaced
