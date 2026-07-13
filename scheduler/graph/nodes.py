"""
Agent nodes for the scheduling graph.

Each domain agent follows the same pattern:
  1. call its deterministic tool to get scored candidates
  2. an LLM selects among the candidates with a rationale (structured output)
  3. if no LLM is available or the selection is invalid, fall back to the
     deterministic top-N pick — the graph always produces a schedule

The LLM never invents times; it only chooses from tool-scored candidates.
"""

import logging

from pydantic import BaseModel, Field

from scheduler.config import UserConfig
from scheduler.models import ScheduleEntry, TimeSlot
from scheduler.graph import tools
from scheduler.graph.state import ScheduleState
from scheduler.week_context import build_week_context

logger = logging.getLogger("agentic_scheduler.graph")


class SlotSelection(BaseModel):
    """LLM's choice among tool-scored candidate slots."""
    indices: list[int] = Field(description="Indices of chosen candidates (0-based)")
    rationale: str = Field(description="One or two sentences on why these slots")


def _select_with_llm(llm, activity: str, goals: str, want: int,
                     candidates: list[TimeSlot]) -> tuple[list[int], str] | None:
    """Ask the LLM to pick `want` slots from candidates. None → use fallback."""
    if llm is None or not candidates:
        return None
    listing = "\n".join(
        f"{i}: {s.day} {s.start_hour}:00 ({s.duration_hours}h) score={s.score} — {'; '.join(s.reasons)}"
        for i, s in enumerate(candidates)
    )
    prompt = (
        f"You are the {activity} scheduling agent. The user's weekly goals:\n"
        f"{goals}\n\n"
        f"Pick exactly {want} slot(s) from these scored candidates (at most one "
        f"per day, prefer higher scores unless the goals suggest otherwise):\n{listing}"
    )
    try:
        sel = llm.with_structured_output(SlotSelection).invoke(prompt)
        indices = [i for i in sel.indices if 0 <= i < len(candidates)]
        # enforce one-per-day and the requested count
        seen_days, valid = set(), []
        for i in indices:
            if candidates[i].day not in seen_days:
                valid.append(i)
                seen_days.add(candidates[i].day)
        if len(valid) == want:
            return valid, sel.rationale
        logger.warning("%s agent: LLM selection invalid (%s picks for want=%s); falling back",
                       activity, len(valid), want)
    except Exception as exc:
        logger.warning("%s agent: LLM selection failed (%s); falling back", activity, exc)
    return None


def _fallback_indices(candidates: list[TimeSlot], want: int) -> list[int]:
    """Deterministic top-N, one per day (the pre-LangGraph behavior)."""
    seen_days, picked = set(), []
    for i, s in enumerate(candidates):
        if len(picked) >= want:
            break
        if s.day in seen_days:
            continue
        picked.append(i)
        seen_days.add(s.day)
    return picked


def _slots_to_entries(candidates: list[TimeSlot], indices: list[int],
                      label: str, category: str) -> list[ScheduleEntry]:
    return [
        ScheduleEntry(
            day=candidates[i].day,
            start_hour=candidates[i].start_hour,
            end_hour=candidates[i].end_hour,
            label=label or candidates[i].activity,
            category=category,
            score=candidates[i].score,
            reasons=candidates[i].reasons,
        )
        for i in indices
    ]


# ── Nodes ───────────────────────────────────────────────────────────────────

def make_parse_node(parse_fn):
    def parse_node(state: ScheduleState) -> dict:
        if state.get("user_config"):
            return {"agent_log": ["[parse] user_config supplied — skipping parse"]}
        config: UserConfig = parse_fn(state["goals"])
        acts = ", ".join(f"{a.name} ({a.category})" for a in config.activities)
        return {"user_config": config, "agent_log": [f"[parse] activities: {acts}"]}
    return parse_node


def context_node(state: ScheduleState) -> dict:
    if state.get("week_context"):
        return {"agent_log": ["[context] week_context supplied — skipping load"]}
    return {"week_context": build_week_context(),
            "agent_log": ["[context] loaded weather / calendar / sleep / busyness"]}


def hard_block_node(state: ScheduleState) -> dict:
    entries = tools.hard_blocks(state["week_context"])
    return {
        "entries": state.get("entries", []) + entries,
        "agent_log": [f"[hard_block] locked {len(entries)} existing event(s)"],
    }


def make_gym_node(llm):
    def gym_node(state: ScheduleState) -> dict:
        config = state["user_config"]
        targets = config.by_category("gym") if config else []
        if config and not targets:
            return {"agent_log": ["[gym] no gym goals — skipped"]}
        want = targets[0].sessions_per_week if targets else 3

        candidates = tools.gym_candidates(state["week_context"], state.get("entries", []), config)
        picked = _select_with_llm(llm, "gym", state.get("goals", ""), want, candidates)
        if picked:
            indices, rationale = picked
            log = f"[gym] LLM selected {len(indices)} slot(s): {rationale}"
        else:
            indices = _fallback_indices(candidates, want)
            log = f"[gym] deterministic top-{len(indices)} selection (no LLM)"
        entries = _slots_to_entries(candidates, indices, "Gym session", "gym")
        return {"entries": state.get("entries", []) + entries, "agent_log": [log]}
    return gym_node


def make_sport_node(llm):
    def sport_node(state: ScheduleState) -> dict:
        config = state["user_config"]
        targets = config.by_category("sport") if config else []
        if not targets:
            return {"agent_log": ["[sport] no sport goals — skipped"]}

        entries = list(state.get("entries", []))
        logs = []
        for target in targets:
            candidates = tools.sport_candidates(state["week_context"], entries, target)
            want = min(target.sessions_per_week, len({c.day for c in candidates}))
            if want == 0:
                logs.append(f"[sport] no viable slot for {target.name}")
                continue
            picked = _select_with_llm(llm, target.name, state.get("goals", ""), want, candidates)
            if picked:
                indices, rationale = picked
                logs.append(f"[sport] LLM picked {target.name}: {rationale}")
            else:
                indices = _fallback_indices(candidates, want)
                logs.append(f"[sport] deterministic pick for {target.name}")
            entries += _slots_to_entries(candidates, indices, target.name.title(), "sport")
        return {"entries": entries, "agent_log": logs}
    return sport_node


def fill_node(state: ScheduleState) -> dict:
    """Fill packing is a bin-packing problem — the deterministic round-robin
    planner is the tool of choice here; no LLM selection layer."""
    entries = tools.fill_plan(state["week_context"], state.get("entries", []),
                              state["user_config"])
    return {
        "entries": state.get("entries", []) + entries,
        "agent_log": [f"[fill] packed {len(entries)} block(s) round-robin across the week"],
    }
