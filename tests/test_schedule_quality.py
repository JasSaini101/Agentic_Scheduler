"""
Schedule-quality assertions: whatever the agents produce must be
conflict-free, meet the requested quotas, respect day pins, and stay
inside the fill window.
"""

import pytest

from scheduler.graph.tools import find_conflicts
from tests.conftest import make_config, gym, sport, fill


def _entries(result, category=None, label=None):
    out = result["entries"]
    if category:
        out = [e for e in out if e.category == category]
    if label:
        out = [e for e in out if e.label.lower() == label.lower()]
    return out


class TestNoOverlaps:
    def test_full_week_is_conflict_free(self, run_graph):
        result = run_graph(make_config(gym(3), sport("tennis", 2), fill(hours=15)))
        assert find_conflicts(result["entries"]) == []

    def test_dense_week_is_conflict_free(self, run_graph):
        result = run_graph(make_config(
            gym(4), sport("tennis", 2), sport("basketball", 1),
            fill("project", hours=20), fill("meal prep", sessions=3, hours=1.5),
        ))
        assert find_conflicts(result["entries"]) == []

    def test_agent_entries_avoid_hard_blocks(self, run_graph):
        result = run_graph(make_config(gym(3), fill(hours=10)))
        for e in result["entries"]:
            if e.category == "hard_block":
                continue
            # weekday work block is 9–17
            if e.day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
                assert e.end_hour <= 9 or e.start_hour >= 17, \
                    f"{e.label} on {e.day} {e.start_hour}–{e.end_hour} overlaps work"


class TestQuotas:
    def test_gym_sessions_match_request(self, run_graph):
        result = run_graph(make_config(gym(3)))
        assert len(_entries(result, "gym")) == 3

    def test_gym_sessions_on_distinct_days(self, run_graph):
        result = run_graph(make_config(gym(3)))
        days = [e.day for e in _entries(result, "gym")]
        assert len(days) == len(set(days))

    def test_sport_sessions_do_not_exceed_request(self, run_graph):
        result = run_graph(make_config(sport("tennis", 2)))
        assert len(_entries(result, "sport")) <= 2

    def test_fill_quota_hours_placed(self, run_graph):
        result = run_graph(make_config(fill("project", hours=12)))
        placed = sum(e.end_hour - e.start_hour for e in _entries(result, "fill"))
        assert placed == pytest.approx(12, abs=0.5)

    def test_fill_spreads_across_multiple_days(self, run_graph):
        result = run_graph(make_config(fill("project", hours=20)))
        days = {e.day for e in _entries(result, "fill")}
        assert len(days) >= 4, f"20h packed into only {days}"


class TestDayPins:
    def test_pinned_fill_lands_on_its_day(self, run_graph):
        result = run_graph(make_config(fill("casino", hours=3, day="Friday"), fill(hours=10)))
        casino = _entries(result, label="Casino")
        assert casino and all(e.day == "Friday" for e in casino)

    def test_pinned_gym_lands_on_its_day(self, run_graph):
        result = run_graph(make_config(gym(1, day="Saturday")))
        entries = _entries(result, "gym")
        assert entries and all(e.day == "Saturday" for e in entries)

    def test_pinned_sport_survives_bad_weather(self, run_graph):
        # Saturday is "storm" in the mock weather — a pin must still schedule
        result = run_graph(make_config(sport("tennis", 1, day="Saturday")))
        entries = _entries(result, "sport")
        assert entries and entries[0].day == "Saturday"
        assert any("anyway" in r for r in entries[0].reasons)

    def test_pinned_activity_beats_flexible_ones_for_the_gap(self, run_graph):
        result = run_graph(make_config(fill("project", hours=25), fill("casino", hours=3, day="Friday")))
        casino = _entries(result, label="Casino")
        assert casino, "pinned activity was squeezed out by flexible fill"


class TestFillWindow:
    def test_fill_stays_inside_window(self, run_graph):
        result = run_graph(make_config(fill(hours=20)))
        for e in _entries(result, "fill"):
            assert e.start_hour >= 8 and e.end_hour <= 22
