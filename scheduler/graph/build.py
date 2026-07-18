"""
Hybrid scheduling graph assembly.

    parse → context → supervisor ⇄ [hard_block, gym, sport, fill]
                                        (agents emit constraints)
                       → solver ──infeasible──→ END (with explanation)
                          │
                          └→ critic ─→ revise ─→ solver … → END

LLM top layer: parse intent, tune objective weights, review the result.
Constraint-solving bottom layer: CP-SAT places every block globally —
overlaps are impossible and quotas/pins are hard constraints.
The critic's feedback loops back as a ConstraintPatch, then re-solves.
"""

import os
from functools import lru_cache

from langgraph.graph import StateGraph, START, END

from scheduler.graph.state import ScheduleState
from scheduler.graph import nodes
from scheduler.graph.critic import make_critic_node, make_revise_node

_AGENT_ORDER = ["hard_block", "gym", "sport", "fill"]

DEFAULT_REVISIONS = 1


def _default_llm():
    """Claude for preference tuning, critic review, and constraint patches;
    None if no API key (deterministic defaults everywhere)."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0)


def _default_parse(goals: str):
    from scheduler.llm.goal_parser import parse_goals
    return parse_goals(goals)


def _supervisor(state: ScheduleState) -> dict:
    return {}   # routing happens in the conditional edge below


def _route_next(state: ScheduleState) -> str:
    """Next domain agent in dependency order; then hand off to the solver."""
    config = state.get("user_config")
    done = set(state.get("done_agents", []))
    for agent in _AGENT_ORDER:
        if agent in done:
            continue
        if agent in ("gym", "sport") and config and not config.by_category(agent):
            continue   # no goals for this domain — skip entirely
        return agent
    return "solver"


def _route_after_solver(state: ScheduleState) -> str:
    return END if state.get("infeasible") else "critic"


def _route_after_critic(state: ScheduleState) -> str:
    if state.get("critic_feedback") and state.get("revisions_left", 0) > 0:
        return "revise"
    return END


def _mark_done(agent_name: str, node_fn):
    def wrapped(state: ScheduleState) -> dict:
        result = node_fn(state)
        result["done_agents"] = state.get("done_agents", []) + [agent_name]
        return result
    return wrapped


def build_graph(llm="default", parse_fn=None, patch_fn=None):
    """Compile the hybrid scheduling graph.

    llm:      chat model for preference tuning / critic / patches
              (pass None to force deterministic behavior — tests/CI)
    parse_fn: goals str → UserConfig  (injectable for tests)
    patch_fn: (feedback, ConstraintSet) → ConstraintPatch  (injectable)
    """
    if llm == "default":
        llm = _default_llm()
    parse_fn = parse_fn or _default_parse

    g = StateGraph(ScheduleState)

    g.add_node("parse", nodes.make_parse_node(parse_fn))
    g.add_node("context", nodes.context_node)
    g.add_node("supervisor", _supervisor)
    g.add_node("hard_block", _mark_done("hard_block", nodes.hard_block_node))
    g.add_node("gym", _mark_done("gym", nodes.make_gym_node(llm)))
    g.add_node("sport", _mark_done("sport", nodes.make_sport_node(llm)))
    g.add_node("fill", _mark_done("fill", nodes.fill_node))
    g.add_node("solver", nodes.solver_node)
    g.add_node("critic", make_critic_node(llm))
    g.add_node("revise", make_revise_node(llm, patch_fn))

    g.add_edge(START, "parse")
    g.add_edge("parse", "context")
    g.add_edge("context", "supervisor")
    g.add_conditional_edges(
        "supervisor", _route_next,
        {"hard_block": "hard_block", "gym": "gym", "sport": "sport",
         "fill": "fill", "solver": "solver"},
    )
    for agent in _AGENT_ORDER:
        g.add_edge(agent, "supervisor")
    g.add_conditional_edges("solver", _route_after_solver, {"critic": "critic", END: END})
    g.add_conditional_edges("critic", _route_after_critic, {"revise": "revise", END: END})
    g.add_edge("revise", "solver")

    return g.compile()


@lru_cache(maxsize=1)
def default_graph():
    return build_graph()


def run_schedule(goals: str) -> tuple[list, object, list[str], object]:
    """Convenience entry: goals → (entries, user_config, agent_log, constraints).

    The final ConstraintSet is returned so callers can apply user change
    requests as constraint patches and re-solve (see scheduler.graph.adjust).
    Raises RuntimeError with the solver's explanation when the requested
    goals cannot fit in the week."""
    result = default_graph().invoke({
        "goals": goals,
        "entries": [],
        "agent_log": [],
        "done_agents": [],
        "revisions_left": DEFAULT_REVISIONS,
    })
    if result.get("infeasible"):
        raise RuntimeError(result["infeasible"])
    return (result["entries"], result["user_config"],
            result.get("agent_log", []), result.get("constraints"))
