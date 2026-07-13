"""
Graph wiring: supervisor routing, deterministic fallbacks, and the
critic → revise conflict-resolution loop.
"""

from scheduler.models import ScheduleEntry
from scheduler.graph.build import build_graph
from tests.conftest import make_config, gym, sport, fill


def test_supervisor_skips_domains_without_goals(run_graph):
    result = run_graph(make_config(gym(2)))
    assert not any(l.startswith("[sport]") for l in result["agent_log"])


def test_supervisor_runs_all_domains_in_order(run_graph):
    result = run_graph(make_config(gym(2), sport(), fill()))
    log = result["agent_log"]
    order = [l.split("]")[0] + "]" for l in log if l.startswith(("[hard_block", "[gym", "[sport", "[fill"))]
    assert order == ["[hard_block]", "[gym]", "[sport]", "[fill]"]


def test_fallback_used_without_llm(run_graph):
    result = run_graph(make_config(gym(2)))
    assert any("deterministic" in l for l in result["agent_log"] if l.startswith("[gym]"))


def test_parse_skipped_when_config_supplied(run_graph):
    result = run_graph(make_config(gym(1)))
    # parse_fn stub runs, but the graph must produce a config either way
    assert result["user_config"] is not None


def test_critic_approves_clean_schedule(run_graph):
    result = run_graph(make_config(gym(2)))
    assert result.get("critic_feedback") is None
    assert any("approved" in l for l in result["agent_log"] if l.startswith("[critic]"))


def test_critic_revision_loop_resolves_conflict():
    cfg = make_config(gym(1))
    calls = {"n": 0}

    def fake_adjust(feedback, entries, config):
        calls["n"] += 1
        return [e for e in entries if e.label != "Clash"]

    clash = [
        ScheduleEntry(day="Monday", start_hour=9, end_hour=11, label="A", category="fill"),
        ScheduleEntry(day="Monday", start_hour=10, end_hour=12, label="Clash", category="fill"),
    ]
    graph = build_graph(llm=None, parse_fn=lambda g: cfg, adjust_fn=fake_adjust)
    result = graph.invoke({
        "goals": "x", "user_config": cfg, "week_context": {"hard_events": []},
        "entries": clash, "agent_log": [],
        "done_agents": ["hard_block", "gym", "sport", "fill"],
        "revisions_left": 1,
    })
    assert calls["n"] == 1
    assert not any(e.label == "Clash" for e in result["entries"])


def test_revision_budget_is_bounded():
    """A conflict the reviser can't fix must not loop forever."""
    cfg = make_config(gym(1))
    calls = {"n": 0}

    def useless_adjust(feedback, entries, config):
        calls["n"] += 1
        return entries   # never fixes anything

    clash = [
        ScheduleEntry(day="Monday", start_hour=9, end_hour=11, label="A", category="fill"),
        ScheduleEntry(day="Monday", start_hour=10, end_hour=12, label="B", category="fill"),
    ]
    graph = build_graph(llm=None, parse_fn=lambda g: cfg, adjust_fn=useless_adjust)
    result = graph.invoke({
        "goals": "x", "user_config": cfg, "week_context": {"hard_events": []},
        "entries": clash, "agent_log": [],
        "done_agents": ["hard_block", "gym", "sport", "fill"],
        "revisions_left": 2,
    })
    assert calls["n"] == 2   # exactly the budget, then stop
    assert result.get("critic_feedback")   # still unresolved, honestly reported


def test_hard_block_overlaps_are_not_conflicts():
    """The user's own nested calendar events (1:1 inside Work) are fine."""
    from scheduler.graph.tools import find_conflicts
    entries = [
        ScheduleEntry(day="Monday", start_hour=9, end_hour=17, label="Work", category="hard_block"),
        ScheduleEntry(day="Monday", start_hour=14, end_hour=15, label="1:1", category="hard_block"),
    ]
    assert find_conflicts(entries) == []
