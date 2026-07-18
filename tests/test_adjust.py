"""
User change requests: NL change → ConstraintPatch → re-solve (hermetic —
the patch LLM is replaced by an injectable patch_fn), plus the web layer's
handling of change-request text typed into the goals box.
"""

import pytest
from fastapi.testclient import TestClient

from scheduler.graph.adjust import apply_change
from scheduler.graph.tools import find_conflicts
from scheduler.models import ScheduleEntry
from scheduler.solver import ActivityRequest, ConstraintPatch, ConstraintSet, HardExclusion, solve

WORK = [HardExclusion(day=d, start_hour=9, end_hour=17, reason="Work")
        for d in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]]


def _base_constraints() -> ConstraintSet:
    return ConstraintSet(
        requests=[
            ActivityRequest(name="gym", category="gym", sessions=2, session_hours=1.5,
                            allowed_hours=[6, 7, 17, 18]),
            ActivityRequest(name="tennis", category="sport", sessions=2, session_hours=1.5),
        ],
        exclusions=WORK,
        fill_window=(6.0, 22.0),
    )


def test_apply_change_resolves_and_keeps_hard_blocks():
    cs = _base_constraints()
    schedule = [ScheduleEntry(day="Monday", start_hour=9, end_hour=17,
                              label="Work", category="hard_block")] + solve(cs).entries

    def patch_fn(change, constraints):
        assert change == "add two gym sessions at 12 or later, tennis back to back"
        return ConstraintPatch(update_requests={
            "gym": {"sessions": 4, "allowed_hours": list(range(12, 21))},
            "tennis": {"pinned_days": ["Saturday", "Sunday"]},
        })

    entries, updated_cs, unplaced = apply_change(
        "add two gym sessions at 12 or later, tennis back to back",
        schedule, cs, patch_fn=patch_fn)

    assert any(e.category == "hard_block" for e in entries)
    gym_entries = [e for e in entries if e.category == "gym"]
    assert len(gym_entries) == 4 and all(e.start_hour >= 12 for e in gym_entries)
    assert sorted(e.day for e in entries if e.category == "sport") == ["Saturday", "Sunday"]
    assert find_conflicts(entries) == []
    assert not unplaced
    # the updated constraints are what future changes patch against
    assert next(r for r in updated_cs.requests if r.name == "gym").sessions == 4


def test_apply_change_requires_llm_or_patch_fn():
    with pytest.raises(ValueError):
        apply_change("whatever", [], _base_constraints())


def test_schedule_endpoint_422_on_change_request_text(monkeypatch):
    """Change-request text typed into the goals box → guidance, not a 500."""
    import web.app as webapp

    monkeypatch.setattr(webapp, "_is_authorized", lambda: True)

    def fake_invoke(payload):
        raise ValueError("Couldn't find any weekly goals in that text.")
    monkeypatch.setattr(webapp.pipeline, "invoke", fake_invoke)

    client = TestClient(webapp.app)
    res = client.post("/api/schedule", json={
        "goals": "Add two gym sessions at 12 or later in the day"})
    assert res.status_code == 422
    assert "weekly goals" in res.json()["detail"]
