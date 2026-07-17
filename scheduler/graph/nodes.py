"""
Agent nodes for the hybrid scheduling graph.

Architecture: LLM top layer + constraint-solving bottom layer.
Each domain agent translates its slice of the problem (goals, weather,
busyness, sleep, the user's existing calendar) into Pydantic constraint
objects — ActivityRequest / HardExclusion / SoftPreference — accumulated in
shared state. The CP-SAT solver node then solves the whole week globally.

When an LLM is available it can ADD tuned preferences with a rationale on
top of the deterministic defaults; without one, the defaults alone drive the
objective. Agents never place events — placement is the solver's job.
"""

import logging

from pydantic import BaseModel, Field

from scheduler.config import ActivityTarget, UserConfig
from scheduler.models import ScheduleEntry
from scheduler.graph.state import ScheduleState
from scheduler.solver import (
    ActivityRequest, ConstraintSet, HardExclusion, SoftPreference, solve, InfeasibleError,
)
from scheduler.week_context import build_week_context

logger = logging.getLogger("agentic_scheduler.graph")


class PreferenceList(BaseModel):
    """Structured output for LLM preference tuning."""
    preferences: list[SoftPreference] = Field(default_factory=list)
    rationale: str = ""


def _llm_preferences(llm, agent: str, goals: str, data_summary: str,
                     activity: str) -> tuple[list[SoftPreference], str] | None:
    """Ask the LLM for extra objective weights; None → defaults only."""
    if llm is None:
        return None
    prompt = (
        f"You are the {agent} scheduling agent in a constraint-solver pipeline. "
        f"The user's goals:\n{goals}\n\nDomain data:\n{data_summary}\n\n"
        f"Emit up to 3 SoftPreference objects for activity '{activity}' that "
        "nudge the solver (positive weight = prefer, negative = avoid, range "
        "-5..5). Only add preferences the data clearly supports."
    )
    try:
        out = llm.with_structured_output(PreferenceList).invoke(prompt)
        prefs = [p.model_copy(update={"activity": activity}) for p in out.preferences[:3]]
        for p in prefs:
            p.weight = max(-5, min(5, p.weight))
        return prefs, out.rationale
    except Exception as exc:
        logger.warning("%s agent: LLM preference tuning failed (%s)", agent, exc)
        return None


def _request_from_target(t: ActivityTarget) -> ActivityRequest:
    # "20h of projects" (1 session × 20h) → quota chunked by the solver;
    # "meal prep 3×1.5h" stays session-based with exact session count.
    quota = t.category == "fill" and t.sessions_per_week == 1 and t.duration_hours > 4
    return ActivityRequest(
        name=t.name,
        category=t.category,
        sessions=t.sessions_per_week,
        session_hours=t.duration_hours,
        total_hours=t.sessions_per_week * t.duration_hours if quota else None,
        pinned_day=t.day,
        allowed_hours=t.candidate_hours if t.category in ("gym", "sport") else [],
    )


def _merge(state: ScheduleState, extra: ConstraintSet) -> ConstraintSet:
    return state.get("constraints", ConstraintSet()).merged(extra)


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
    """Existing calendar events → display entries + solver exclusions."""
    from scheduler.data.mock_week import HARD_EVENTS
    events = state["week_context"].get("hard_events", HARD_EVENTS)
    entries = [
        ScheduleEntry(day=ev["day"], start_hour=ev["start_hour"],
                      end_hour=ev["end_hour"], label=ev["label"], category="hard_block")
        for ev in events
    ]
    exclusions = [
        HardExclusion(day=ev["day"], start_hour=ev["start_hour"],
                      end_hour=ev["end_hour"], reason=ev["label"])
        for ev in events
    ]
    config = state.get("user_config")
    window = tuple(map(float, config.fill_window)) if config else (8.0, 22.0)
    return {
        "entries": state.get("entries", []) + entries,
        "constraints": _merge(state, ConstraintSet(exclusions=exclusions, fill_window=window)),
        "agent_log": [f"[hard_block] emitted {len(exclusions)} exclusion(s) from the calendar"],
    }


def make_gym_node(llm):
    def gym_node(state: ScheduleState) -> dict:
        config = state["user_config"]
        targets = config.by_category("gym") if config else []
        if not targets:
            return {"agent_log": ["[gym] no gym goals — skipped"]}
        target = targets[0]

        from scheduler.data.mock_week import GYM_BUSYNESS, SLEEP_SCORES
        sleep = state["week_context"].get("sleep_scores", SLEEP_SCORES)

        prefs: list[SoftPreference] = []
        # busyness → per-hour weights (quiet hours get a bonus)
        by_hour: dict[int, list[float]] = {}
        for (_, hour), busy in GYM_BUSYNESS.items():
            by_hour.setdefault(hour, []).append(busy)
        for hour, vals in by_hour.items():
            avg = sum(vals) / len(vals)
            weight = max(-3, min(3, round((0.6 - avg) * 6)))
            if weight:
                prefs.append(SoftPreference(
                    activity=target.name, hour_start=hour, hour_end=hour + 1,
                    weight=weight,
                    reason=f"Gym ~{int(avg * 100)}% full around {hour}:00"))
        # poor sleep → avoid early sessions that day
        for day, score in sleep.items():
            if score <= 5:
                prefs.append(SoftPreference(
                    activity=target.name, day=day, hour_start=0, hour_end=9,
                    weight=-2, reason=f"Sleep score {score}/10 on {day} — avoid early workout"))

        logs = [f"[gym] emitted request ({target.sessions_per_week}×{target.duration_hours}h) "
                f"+ {len(prefs)} preference(s)"]
        tuned = _llm_preferences(llm, "gym", state.get("goals", ""),
                                 f"busyness by hour: {by_hour}\nsleep scores: {sleep}",
                                 target.name)
        if tuned:
            extra, rationale = tuned
            prefs += extra
            logs.append(f"[gym] LLM tuned {len(extra)} preference(s): {rationale}")

        return {
            "constraints": _merge(state, ConstraintSet(
                requests=[_request_from_target(target)], preferences=prefs)),
            "agent_log": logs,
        }
    return gym_node


def make_sport_node(llm):
    def sport_node(state: ScheduleState) -> dict:
        config = state["user_config"]
        targets = config.by_category("sport") if config else []
        if not targets:
            return {"agent_log": ["[sport] no sport goals — skipped"]}

        from scheduler.data.mock_week import WEATHER
        weather = state["week_context"].get("weather", WEATHER)

        requests, exclusions, prefs, logs = [], [], [], []
        for target in targets:
            requests.append(_request_from_target(target))
            for day, w in weather.items():
                cond = w.get("condition", "clear")
                if cond == "storm":
                    if target.day == day:
                        prefs.append(SoftPreference(
                            activity=target.name, day=day, weight=-5,
                            reason=f"Storm forecast on {day} — scheduled anyway at your request"))
                    else:
                        exclusions.append(HardExclusion(
                            day=day, start_hour=0, end_hour=24,
                            activity=target.name,
                            reason=f"Storm forecast on {day}"))
                elif cond == "clear":
                    prefs.append(SoftPreference(
                        activity=target.name, day=day, weight=2,
                        reason=f"Clear weather on {day} ({w.get('temp_f', '?')}°F)"))
                elif cond == "rain":
                    prefs.append(SoftPreference(
                        activity=target.name, day=day, weight=-2,
                        reason=f"Rain forecast on {day}"))
            prefs.append(SoftPreference(
                activity=target.name, hour_start=8, hour_end=11, weight=1,
                reason="Morning slots preferred for outdoor sport"))
            logs.append(f"[sport] emitted request for {target.name} "
                        f"({target.sessions_per_week}×{target.duration_hours}h)")

            tuned = _llm_preferences(llm, "sport", state.get("goals", ""),
                                     f"weather forecast: {weather}", target.name)
            if tuned:
                extra, rationale = tuned
                prefs += extra
                logs.append(f"[sport] LLM tuned {len(extra)} preference(s): {rationale}")

        return {
            "constraints": _merge(state, ConstraintSet(
                requests=requests, exclusions=exclusions, preferences=prefs)),
            "agent_log": logs,
        }
    return sport_node


def fill_node(state: ScheduleState) -> dict:
    config = state["user_config"]
    targets = config.by_category("fill") if config else []
    if not targets:
        return {"agent_log": ["[fill] no fill goals — skipped"]}
    requests = [_request_from_target(t) for t in targets]
    return {
        "constraints": _merge(state, ConstraintSet(requests=requests)),
        "agent_log": [f"[fill] emitted {len(requests)} request(s)"],
    }


def solver_node(state: ScheduleState) -> dict:
    """The constraint-solving layer: globally optimal, conflict-free placement.

    Places as much of every request as physically fits; shortfalls are
    reported honestly in the log instead of dropping sessions silently."""
    cs = state.get("constraints", ConstraintSet())
    hard = [e for e in state.get("entries", []) if e.category == "hard_block"]
    try:
        result = solve(cs)
    except InfeasibleError as exc:
        return {
            "entries": hard,
            "infeasible": str(exc),
            "agent_log": [f"[solver] infeasible: {exc}"],
        }
    logs = [f"[solver] placed {len(result.entries)} block(s) from "
            f"{len(cs.requests)} request(s), {len(cs.preferences)} preference(s)"]
    logs += [f"[solver] could not fit: {u}" for u in result.unplaced]
    return {
        "entries": hard + result.entries,
        "infeasible": None,
        "agent_log": logs,
    }
