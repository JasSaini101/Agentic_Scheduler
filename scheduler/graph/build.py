"""
Scheduling graph assembly.

    parse → context → supervisor ─→ hard_block ─┐
                          ▲                     │
                          └─────────────────────┘   (routes gym / sport / fill
                          │                          based on parsed goals)
                          └→ critic ─→ revise ─→ critic … → END

The supervisor routes to each domain agent in dependency order, skipping
agents with no matching goals. The critic reviews the finished schedule and
can send it through a bounded revision loop.
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
    """Claude for agent slot-selection and critic review; None if no API key
    (every node then falls back to its deterministic path)."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0)


def _default_parse(goals: str):
    from scheduler.llm.goal_parser import parse_goals
    return parse_goals(goals)


def _default_adjust(change_text, entries, user_config):
    from scheduler.llm.adjustment_handler import apply
    return apply(change_text, entries, user_config, sync_calendar=False)


def _supervisor(state: ScheduleState) -> dict:
    return {}   # routing happens in the conditional edge below


def _route_next(state: ScheduleState) -> str:
    """Pick the next domain agent in dependency order; then hand to critic."""
    config = state.get("user_config")
    done = set(state.get("done_agents", []))
    for agent in _AGENT_ORDER:
        if agent in done:
            continue
        if agent in ("gym", "sport") and config and not config.by_category(agent):
            continue   # no goals for this domain — skip entirely
        return agent
    return "critic"


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


def build_graph(llm="default", parse_fn=None, adjust_fn=None):
    """Compile the scheduling graph.

    llm:       chat model for agent selection + critic (pass None to force the
               deterministic fallbacks — used in tests/CI)
    parse_fn:  goals str → UserConfig  (injectable for tests)
    adjust_fn: (feedback, entries, config) → entries  (injectable for tests)
    """
    if llm == "default":
        llm = _default_llm()
    parse_fn = parse_fn or _default_parse
    adjust_fn = adjust_fn or _default_adjust

    g = StateGraph(ScheduleState)

    g.add_node("parse", nodes.make_parse_node(parse_fn))
    g.add_node("context", nodes.context_node)
    g.add_node("supervisor", _supervisor)
    g.add_node("hard_block", _mark_done("hard_block", nodes.hard_block_node))
    g.add_node("gym", _mark_done("gym", nodes.make_gym_node(llm)))
    g.add_node("sport", _mark_done("sport", nodes.make_sport_node(llm)))
    g.add_node("fill", _mark_done("fill", nodes.fill_node))
    g.add_node("critic", make_critic_node(llm))
    g.add_node("revise", make_revise_node(adjust_fn))

    g.add_edge(START, "parse")
    g.add_edge("parse", "context")
    g.add_edge("context", "supervisor")
    g.add_conditional_edges(
        "supervisor", _route_next,
        {"hard_block": "hard_block", "gym": "gym", "sport": "sport",
         "fill": "fill", "critic": "critic"},
    )
    for agent in _AGENT_ORDER:
        g.add_edge(agent, "supervisor")
    g.add_conditional_edges("critic", _route_after_critic,
                            {"revise": "revise", END: END})
    g.add_edge("revise", "critic")

    return g.compile()


@lru_cache(maxsize=1)
def default_graph():
    return build_graph()


def run_schedule(goals: str) -> tuple[list, object, list[str]]:
    """Convenience entry: goals → (entries, user_config, agent_log)."""
    result = default_graph().invoke({
        "goals": goals,
        "entries": [],
        "agent_log": [],
        "done_agents": [],
        "revisions_left": DEFAULT_REVISIONS,
    })
    return result["entries"], result["user_config"], result.get("agent_log", [])
