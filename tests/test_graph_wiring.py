"""
Hybrid graph wiring: supervisor routing, solver hand-off, deterministic
operation without an LLM, and the critic → patch → re-solve loop.
"""

from scheduler.models import ScheduleEntry
from scheduler.graph.build import build_graph
from scheduler.solver import ConstraintPatch
from tests.conftest import WEEK_CONTEXT, make_config, gym, sport, fill


def test_supervisor_skips_domains_without_goals(run_graph):
    result = run_graph(make_config(gym(2)))
    assert not any(l.startswith("[sport]") for l in result["agent_log"])


def test_supervisor_runs_agents_then_solver(run_graph):
    result = run_graph(make_config(gym(2), sport(), fill()))
    log = result["agent_log"]
    seen = []
    for l in log:
        prefix = l.split("]")[0] + "]"
        if prefix in ("[hard_block]", "[gym]", "[sport]", "[fill]", "[solver]") \
                and prefix not in seen:
            seen.append(prefix)
    assert seen == ["[hard_block]", "[gym]", "[sport]", "[fill]", "[solver]"]


def test_agents_emit_constraints(run_graph):
    result = run_graph(make_config(gym(2), fill(hours=10)))
    cs = result["constraints"]
    names = {r.name for r in cs.requests}
    assert names == {"gym", "personal project"}
    assert len(cs.exclusions) == 5          # weekday work blocks
    assert any(p.weight for p in cs.preferences)   # busyness/sleep weights emitted


def test_runs_fully_without_llm(run_graph):
    result = run_graph(make_config(gym(2)))
    assert result["entries"]
    assert result.get("infeasible") is None


def test_critic_approves_clean_schedule(run_graph):
    result = run_graph(make_config(gym(2)))
    assert result.get("critic_feedback") is None
    assert any("approved" in l for l in result["agent_log"] if l.startswith("[critic]"))


def test_oversubscription_reports_shortfall_in_log(run_graph):
    result = run_graph(make_config(fill("work", hours=70), fill("study", hours=70)))
    assert result.get("infeasible") is None       # partial schedule, not a failure
    assert any("could not fit" in l for l in result["agent_log"])


class _FakeStructured:
    def __init__(self, schema, owner):
        self.schema, self.owner = schema, owner

    def invoke(self, prompt):
        from scheduler.graph.critic import CriticVerdict
        from scheduler.graph.nodes import PreferenceList
        if self.schema is CriticVerdict:
            self.owner.critic_calls += 1
            if self.owner.critic_calls == 1:
                return CriticVerdict(approved=False, feedback="move gym to Saturday")
            return CriticVerdict(approved=True)
        if self.schema is PreferenceList:
            return PreferenceList()
        raise AssertionError(f"unexpected schema {self.schema}")


class FakeLLM:
    """Critic rejects once, then approves; preference tuning is a no-op."""
    def __init__(self):
        self.critic_calls = 0

    def with_structured_output(self, schema):
        return _FakeStructured(schema, self)


def test_critic_patch_resolve_loop():
    """Critic feedback → ConstraintPatch → re-solve → approval."""
    cfg = make_config(gym(1))
    llm = FakeLLM()
    patches = []

    def patch_fn(feedback, cs):
        patches.append(feedback)
        return ConstraintPatch(pin_activity={"gym": "Saturday"})

    graph = build_graph(llm=llm, parse_fn=lambda g: cfg, patch_fn=patch_fn)
    result = graph.invoke({
        "goals": "gym once", "week_context": dict(WEEK_CONTEXT),
        "entries": [], "agent_log": [], "done_agents": [], "revisions_left": 1,
    })
    assert patches == ["move gym to Saturday"]
    gym_entries = [e for e in result["entries"] if e.category == "gym"]
    assert gym_entries and gym_entries[0].day == "Saturday"
    assert llm.critic_calls == 2   # reject, then approve after re-solve


def test_revision_budget_is_bounded():
    """A critic that always rejects must stop at the revision budget."""
    class AlwaysReject(FakeLLM):
        def with_structured_output(self, schema):
            outer = self

            class S(_FakeStructured):
                def invoke(self, prompt):
                    from scheduler.graph.critic import CriticVerdict
                    from scheduler.graph.nodes import PreferenceList
                    if self.schema is CriticVerdict:
                        outer.critic_calls += 1
                        return CriticVerdict(approved=False, feedback="still bad")
                    return PreferenceList()
            return S(schema, outer)

    cfg = make_config(gym(1))
    calls = {"n": 0}

    def patch_fn(feedback, cs):
        calls["n"] += 1
        return ConstraintPatch()

    graph = build_graph(llm=AlwaysReject(), parse_fn=lambda g: cfg, patch_fn=patch_fn)
    result = graph.invoke({
        "goals": "gym once", "week_context": dict(WEEK_CONTEXT),
        "entries": [], "agent_log": [], "done_agents": [], "revisions_left": 2,
    })
    assert calls["n"] == 2                    # exactly the budget
    assert result.get("critic_feedback")      # still unresolved, honestly reported


def test_hard_block_overlaps_are_not_conflicts():
    from scheduler.graph.tools import find_conflicts
    entries = [
        ScheduleEntry(day="Monday", start_hour=9, end_hour=17, label="Work", category="hard_block"),
        ScheduleEntry(day="Monday", start_hour=14, end_hour=15, label="1:1", category="hard_block"),
    ]
    assert find_conflicts(entries) == []
