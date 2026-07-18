"""
CP-SAT solver unit tests — the constraint-solving layer in isolation.
"""

import pytest

from scheduler.graph.tools import find_conflicts
from scheduler.solver import (
    ActivityRequest, ConstraintPatch, ConstraintSet, HardExclusion,
    SoftPreference, InfeasibleError, solve,
)

WORKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
WORK = [HardExclusion(day=d, start_hour=9, end_hour=17, reason="Work") for d in WORKDAYS]


def hours(entries, label=None):
    sel = [e for e in entries if label is None or e.label.lower() == label.lower()]
    return sum(e.end_hour - e.start_hour for e in sel)


def test_no_overlaps_by_construction():
    cs = ConstraintSet(
        requests=[
            ActivityRequest(name="gym", category="gym", sessions=4, session_hours=1.5,
                            allowed_hours=[6, 7, 17, 18]),
            ActivityRequest(name="tennis", category="sport", sessions=2, session_hours=1.5,
                            allowed_hours=[8, 9, 10]),
            ActivityRequest(name="project", category="fill", total_hours=20),
            ActivityRequest(name="meal prep", category="fill", sessions=3, session_hours=1.5),
        ],
        exclusions=WORK,
        fill_window=(6.0, 22.0),
    )
    entries = solve(cs).entries
    assert find_conflicts(entries) == []
    # exclusions respected too
    for e in entries:
        if e.day in WORKDAYS:
            assert e.end_hour <= 9 or e.start_hour >= 17


def test_quotas_exact():
    cs = ConstraintSet(
        requests=[
            ActivityRequest(name="gym", category="gym", sessions=3, session_hours=1.5),
            ActivityRequest(name="project", category="fill", total_hours=12),
        ],
        fill_window=(8.0, 22.0),
    )
    entries = solve(cs).entries
    assert len([e for e in entries if e.category == "gym"]) == 3
    assert hours(entries, "Project") == pytest.approx(12)


def test_session_days_distinct():
    cs = ConstraintSet(
        requests=[ActivityRequest(name="gym", category="gym", sessions=4, session_hours=1.5)],
        fill_window=(8.0, 22.0),
    )
    days = [e.day for e in solve(cs).entries]
    assert len(days) == len(set(days)) == 4


def test_quota_spreads_across_days():
    """The greedy-scheduler regression: 20h must not pile into two days."""
    cs = ConstraintSet(
        requests=[ActivityRequest(name="project", category="fill", total_hours=20)],
        exclusions=WORK,
        fill_window=(8.0, 22.0),
    )
    entries = solve(cs).entries
    assert len({e.day for e in entries}) >= 5


def test_quota_can_double_up_when_week_is_tight():
    """Spread is a preference, not a wall — a packed week still solves."""
    tight = WORK + [HardExclusion(day=d, start_hour=17, end_hour=22, reason="Busy")
                    for d in ["Monday", "Tuesday", "Wednesday"]]
    cs = ConstraintSet(
        requests=[ActivityRequest(name="project", category="fill", total_hours=24)],
        exclusions=tight,
        fill_window=(8.0, 22.0),
    )
    entries = solve(cs).entries
    assert hours(entries, "Project") == pytest.approx(24)
    assert find_conflicts(entries) == []


def test_pin_is_hard():
    cs = ConstraintSet(
        requests=[
            ActivityRequest(name="casino", category="fill", sessions=1, session_hours=3,
                            pinned_day="Friday"),
            ActivityRequest(name="project", category="fill", total_hours=25),
        ],
        exclusions=WORK,
        fill_window=(8.0, 22.0),
    )
    entries = solve(cs).entries
    casino = [e for e in entries if e.label == "Casino"]
    assert casino and casino[0].day == "Friday"


def test_allowed_hours_respected():
    cs = ConstraintSet(
        requests=[ActivityRequest(name="gym", category="gym", sessions=3, session_hours=1.5,
                                  allowed_hours=[6, 7, 17, 18])],
        fill_window=(6.0, 22.0),
    )
    for e in solve(cs).entries:
        assert e.start_hour in (6, 7, 17, 18)


def test_preferences_steer_placement():
    base = ConstraintSet(
        requests=[ActivityRequest(name="gym", category="gym", sessions=1, session_hours=1,
                                  allowed_hours=[6, 18])],
        fill_window=(6.0, 22.0),
    )
    morning = base.model_copy(update={"preferences": [
        SoftPreference(activity="gym", hour_start=6, hour_end=8, weight=5, reason="quiet")]})
    evening = base.model_copy(update={"preferences": [
        SoftPreference(activity="gym", hour_start=17, hour_end=20, weight=5, reason="pump")]})
    assert solve(morning).entries[0].start_hour == 6
    assert solve(evening).entries[0].start_hour == 18


def test_activity_scoped_exclusion():
    cs = ConstraintSet(
        requests=[ActivityRequest(name="tennis", category="sport", sessions=1, session_hours=1.5,
                                  allowed_hours=[8, 9, 10])],
        exclusions=[HardExclusion(day=d, start_hour=0, end_hour=24,
                                  activity="tennis", reason="storm")
                    for d in WORKDAYS + ["Saturday"]],
        fill_window=(8.0, 22.0),
    )
    entries = solve(cs).entries
    assert entries[0].day == "Sunday"


def test_oversubscription_reports_shortfall():
    """Too many hours → place what fits, report the rest (never silent drops)."""
    cs = ConstraintSet(
        requests=[
            ActivityRequest(name="work", category="fill", total_hours=27),
            ActivityRequest(name="study", category="fill", total_hours=27),
        ],
        exclusions=WORK + [HardExclusion(day=d, start_hour=17, end_hour=22, reason="x")
                           for d in WORKDAYS],
        fill_window=(8.0, 22.0),
    )
    result = solve(cs)
    assert result.unplaced, "shortfall must be reported"
    assert find_conflicts(result.entries) == []
    # weekend-only capacity: 2 days × 14h = 28h — placed hours must not exceed it
    assert hours(result.entries) <= 28.01


def test_impossible_block_raises():
    """A single block larger than the daily window is structurally impossible."""
    cs = ConstraintSet(
        requests=[ActivityRequest(name="marathon", category="fill",
                                  sessions=1, session_hours=20)],
        fill_window=(8.0, 22.0),
    )
    with pytest.raises(InfeasibleError):
        solve(cs)


def test_pinned_days_per_session():
    """'Back to back days' — each session hard-pinned to its own day."""
    cs = ConstraintSet(
        requests=[ActivityRequest(name="tennis", category="sport", sessions=2,
                                  session_hours=1.5,
                                  pinned_days=["Saturday", "Sunday"])],
        fill_window=(8.0, 22.0),
    )
    days = sorted(e.day for e in solve(cs).entries)
    assert days == ["Saturday", "Sunday"]


def test_patch_update_requests_resolves_change():
    """The failing user scenario: +2 gym sessions at 12:00 or later, tennis
    on consecutive days — expressed as a ConstraintPatch and re-solved."""
    cs = ConstraintSet(
        requests=[
            ActivityRequest(name="gym", category="gym", sessions=2, session_hours=1.5,
                            allowed_hours=[6, 7, 17, 18]),
            ActivityRequest(name="tennis", category="sport", sessions=2, session_hours=1.5),
        ],
        exclusions=WORK,
        fill_window=(6.0, 22.0),
    )
    patch = ConstraintPatch(update_requests={
        "gym": {"sessions": 4, "allowed_hours": list(range(12, 21))},
        "tennis": {"pinned_days": ["Saturday", "Sunday"]},
    })
    entries = solve(patch.apply(cs)).entries
    gym_entries = [e for e in entries if e.category == "gym"]
    assert len(gym_entries) == 4
    assert all(e.start_hour >= 12 for e in gym_entries)
    assert sorted(e.day for e in entries if e.category == "sport") == ["Saturday", "Sunday"]
    assert find_conflicts(entries) == []


def test_patch_add_and_remove_requests():
    cs = ConstraintSet(
        requests=[ActivityRequest(name="gym", category="gym", sessions=2, session_hours=1)],
        fill_window=(8.0, 22.0),
    )
    patch = ConstraintPatch(
        add_requests=[ActivityRequest(name="yoga", category="fill",
                                      sessions=1, session_hours=1)],
        remove_activities=["gym"],
    )
    entries = solve(patch.apply(cs)).entries
    assert {e.label for e in entries} == {"Yoga"}


def test_constraint_patch_pins_activity():
    cs = ConstraintSet(
        requests=[ActivityRequest(name="gym", category="gym", sessions=1, session_hours=1.5)],
        fill_window=(8.0, 22.0),
    )
    patched = ConstraintPatch(pin_activity={"gym": "Saturday"}).apply(cs)
    assert patched.requests[0].pinned_day == "Saturday"
    assert solve(patched).entries[0].day == "Saturday"
