"""
Critic node — reviews the assembled schedule against the user's goals.

The critic gets hard signals from the deterministic conflict validator plus
the raw goals, and produces a structured verdict. If it requests changes and
revision budget remains, the graph routes to the revise node, which applies
the feedback through the NL adjustment engine, then returns to the critic.
"""

import logging

from pydantic import BaseModel, Field

from scheduler.graph import tools
from scheduler.graph.state import ScheduleState

logger = logging.getLogger("agentic_scheduler.graph")


class CriticVerdict(BaseModel):
    approved: bool = Field(description="True if the schedule meets the goals")
    feedback: str = Field(default="", description=(
        "If not approved: a single concrete change request phrased as a user "
        "instruction, e.g. 'move gym on Monday to Saturday'"))


def _summarize(entries) -> str:
    return "\n".join(
        f"- {e.day} {e.start_hour}:00–{e.end_hour} {e.label} ({e.category})"
        for e in sorted(entries, key=lambda e: (e.day, e.start_hour))
    )


def make_critic_node(llm):
    def critic_node(state: ScheduleState) -> dict:
        entries = state.get("entries", [])
        conflicts = tools.find_conflicts(entries)

        # Hard failures don't need an LLM opinion
        if conflicts:
            return {
                "critic_feedback": "Fix the overlapping events: " + "; ".join(conflicts),
                "agent_log": [f"[critic] found {len(conflicts)} conflict(s)"],
            }

        if llm is None:
            return {"critic_feedback": None,
                    "agent_log": ["[critic] no LLM — conflict check only, approved"]}

        prompt = (
            "You are a schedule reviewer. The user's weekly goals:\n"
            f"{state.get('goals', '')}\n\n"
            "The proposed schedule (already conflict-free):\n"
            f"{_summarize(entries)}\n\n"
            "Approve unless something clearly violates the goals (wrong day for a "
            "pinned activity, a requested activity missing entirely). Minor "
            "style preferences are not grounds for rejection."
        )
        try:
            verdict = llm.with_structured_output(CriticVerdict).invoke(prompt)
        except Exception as exc:
            logger.warning("critic LLM failed (%s); approving on conflict check alone", exc)
            return {"critic_feedback": None,
                    "agent_log": [f"[critic] LLM unavailable ({exc}); approved on validators"]}

        if verdict.approved or not verdict.feedback:
            return {"critic_feedback": None, "agent_log": ["[critic] approved"]}
        return {
            "critic_feedback": verdict.feedback,
            "agent_log": [f"[critic] requested revision: {verdict.feedback}"],
        }
    return critic_node


def make_revise_node(llm, patch_fn=None):
    """Turn critic feedback into a ConstraintPatch and apply it — the solver
    then re-solves with the edited constraints (no hand-editing of entries).

    patch_fn(feedback, constraints) → ConstraintPatch is injectable for tests;
    by default the LLM emits the patch as structured output."""
    from scheduler.solver import ConstraintPatch

    def _llm_patch(feedback: str, cs) -> "ConstraintPatch":
        summary = "\n".join(f"- {r.name} ({r.category})"
                            + (f", pinned {r.pinned_day}" if r.pinned_day else "")
                            for r in cs.requests)
        prompt = (
            "You maintain the constraint set of a schedule solver. Current "
            f"activity requests:\n{summary}\n\nReviewer feedback:\n{feedback}\n\n"
            "Emit a ConstraintPatch implementing the feedback: pin activities "
            "to days via pin_activity, block time ranges via add_exclusions, "
            "or nudge with add_preferences (weights -5..5). Smallest change wins."
        )
        return llm.with_structured_output(ConstraintPatch).invoke(prompt)

    def revise_node(state: ScheduleState) -> dict:
        feedback = state.get("critic_feedback") or ""
        cs = state.get("constraints")
        make_patch = patch_fn or (_llm_patch if llm is not None else None)
        if make_patch is None or cs is None:
            return {
                "revisions_left": 0,
                "critic_feedback": None,
                "agent_log": ["[revise] no LLM to build a constraint patch — keeping schedule"],
            }
        try:
            patch = make_patch(feedback, cs)
            updated = patch.apply(cs)
        except Exception as exc:
            logger.warning("revision failed (%s); keeping schedule as-is", exc)
            return {
                "revisions_left": 0,
                "critic_feedback": None,
                "agent_log": [f"[revise] failed ({exc}); keeping current schedule"],
            }
        return {
            "constraints": updated,
            "revisions_left": state.get("revisions_left", 0) - 1,
            "critic_feedback": None,
            "agent_log": [f"[revise] patched constraints per feedback: {feedback}"],
        }
    return revise_node
