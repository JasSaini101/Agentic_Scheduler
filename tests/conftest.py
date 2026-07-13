"""
Shared fixtures for the scheduling test suite.

Graph tests run with llm=None (deterministic fallbacks) and a fixed
week_context, so they are fast, hermetic, and CI-safe — no API keys, no
network. The goal-parser evals in test_goal_parser_evals.py are the only
tests that call a real LLM, and they skip without ANTHROPIC_API_KEY.
"""

import pytest

from scheduler.config import ActivityTarget, UserConfig
from scheduler.graph.build import build_graph, DEFAULT_REVISIONS


HARD_EVENTS = [
    {"day": "Monday",    "start_hour": 9,  "end_hour": 17, "label": "Work", "type": "work"},
    {"day": "Tuesday",   "start_hour": 9,  "end_hour": 17, "label": "Work", "type": "work"},
    {"day": "Wednesday", "start_hour": 9,  "end_hour": 17, "label": "Work", "type": "work"},
    {"day": "Thursday",  "start_hour": 9,  "end_hour": 17, "label": "Work", "type": "work"},
    {"day": "Friday",    "start_hour": 9,  "end_hour": 17, "label": "Work", "type": "work"},
]

WEEK_CONTEXT = {"hard_events": HARD_EVENTS}


def make_config(*activities: ActivityTarget) -> UserConfig:
    return UserConfig(activities=list(activities), fill_window=(8, 22))


def gym(sessions=3, hours=1.5, day=None):
    return ActivityTarget(name="gym", sessions_per_week=sessions, duration_hours=hours,
                          candidate_hours=[6, 7, 17, 18], category="gym", day=day)


def sport(name="tennis", sessions=2, hours=1.5, day=None):
    return ActivityTarget(name=name, sessions_per_week=sessions, duration_hours=hours,
                          candidate_hours=[8, 9, 10], category="sport", day=day)


def fill(name="personal project", sessions=1, hours=10.0, day=None):
    return ActivityTarget(name=name, sessions_per_week=sessions, duration_hours=hours,
                          candidate_hours=[], category="fill", day=day)


@pytest.fixture
def run_graph():
    """Run the full graph for a config with deterministic agents."""
    def _run(config: UserConfig, revisions: int = DEFAULT_REVISIONS,
             adjust_fn=None, week_context=None):
        graph = build_graph(
            llm=None,
            parse_fn=lambda goals: config,
            adjust_fn=adjust_fn or (lambda fb, entries, cfg: entries),
        )
        return graph.invoke({
            "goals": config.raw_goals or "test goals",
            "week_context": week_context or dict(WEEK_CONTEXT),
            "entries": [],
            "agent_log": [],
            "done_agents": [],
            "revisions_left": revisions,
        })
    return _run
