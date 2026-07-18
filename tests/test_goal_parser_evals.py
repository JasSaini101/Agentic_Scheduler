"""
Goal-parser evals — the only tests that call a real LLM.

Each case is (goals string → expected structure). Skipped entirely when
ANTHROPIC_API_KEY is not set, so CI stays green without secrets; run them
locally (or in CI with the secret configured) to catch prompt regressions.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="goal-parser evals need ANTHROPIC_API_KEY",
)


def _parse(goals: str):
    from scheduler.llm.goal_parser import parse_goals
    return parse_goals(goals)


def _by_cat(cfg, cat):
    return cfg.by_category(cat)


# (goals, expected) — expected keys:
#   gym_sessions, sport_names, fill_names, pinned {name: day}, n_activities
EVAL_CASES = [
    ("gym 3x a week",
     {"gym_sessions": 3}),
    ("go to the gym four times",
     {"gym_sessions": 4}),
    ("tennis twice a week",
     {"sport_names": ["tennis"]}),
    ("tennis twice, basketball once",
     {"sport_names": ["tennis", "basketball"]}),
    ("20h of personal projects",
     {"fill_quota": ("personal project", 20.0)}),
    ("gym 3x, tennis twice, 15 hours studying",
     {"gym_sessions": 3, "sport_names": ["tennis"], "n_activities": 3}),
    ("casino on Friday",
     {"pinned": {"casino": "Friday"}}),
    ("gym on Monday, tennis on Saturday",
     {"pinned": {"gym": "Monday", "tennis": "Saturday"}}),
    ("weights 3 times a week and a long run on Sunday",
     {"gym_sessions": 3, "pinned_days": ["Sunday"]}),
    ("I want to lift twice and play soccer once this week",
     {"gym_sessions": 2, "sport_names": ["soccer"]}),
    ("study for finals 25 hours",
     {"fill_quota_hours": 25.0}),
    ("meal prep twice for 2 hours each",
     {"fill_sessions": 2}),
    ("gym every weekday morning",
     {"gym_sessions": 5}),
    ("basketball on Wednesday evening",
     {"pinned": {"basketball": "Wednesday"}}),
    ("10 hours coding, 5 hours reading",
     {"n_activities": 2}),
    ("swim twice, gym once",
     {"gym_sessions": 1, "sport_names": ["swim"]}),
    ("dinner with parents on Thursday",
     {"pinned_days": ["Thursday"]}),
    ("CrossFit 3x",
     {"gym_sessions": 3}),
    ("tennis Saturday morning and Sunday morning",
     {"sport_names": ["tennis"]}),
    ("work on my portfolio site for 12 hours and hit the gym twice",
     {"gym_sessions": 2, "fill_quota_hours": 12.0}),
    ("3 tennis sessions, one on Monday and one on Friday",
     {"session_days": {"tennis": (3, ["Monday", "Friday"])}}),
    ("play tennis on Saturday and Sunday",
     {"session_days": {"tennis": (2, ["Saturday", "Sunday"])}}),
]


@pytest.mark.parametrize("goals,expected", EVAL_CASES, ids=[c[0][:40] for c in EVAL_CASES])
def test_goal_parsing(goals, expected):
    cfg = _parse(goals)
    assert cfg.activities, f"no activities parsed from: {goals}"

    if "n_activities" in expected:
        assert len(cfg.activities) == expected["n_activities"]

    if "gym_sessions" in expected:
        gyms = _by_cat(cfg, "gym")
        assert gyms, "expected a gym activity"
        assert gyms[0].sessions_per_week == expected["gym_sessions"]

    if "sport_names" in expected:
        names = {a.name.lower() for a in _by_cat(cfg, "sport")}
        for want in expected["sport_names"]:
            assert any(want in n for n in names), f"missing sport {want} in {names}"

    if "fill_quota" in expected:
        name, hours = expected["fill_quota"]
        fills = _by_cat(cfg, "fill")
        assert fills
        total = fills[0].sessions_per_week * fills[0].duration_hours
        assert total == pytest.approx(hours, rel=0.25)

    if "fill_quota_hours" in expected:
        fills = _by_cat(cfg, "fill")
        assert fills
        totals = [a.sessions_per_week * a.duration_hours for a in fills]
        assert any(t == pytest.approx(expected["fill_quota_hours"], rel=0.25) for t in totals)

    if "fill_sessions" in expected:
        fills = _by_cat(cfg, "fill")
        assert fills and fills[0].sessions_per_week == expected["fill_sessions"]

    if "pinned" in expected:
        by_name = {a.name.lower(): a for a in cfg.activities}
        for name, day in expected["pinned"].items():
            match = next((a for k, a in by_name.items() if name in k), None)
            assert match is not None, f"activity {name} not parsed"
            assert match.day == day, f"{name} pinned to {match.day}, expected {day}"

    if "session_days" in expected:
        # one activity with the full session count; the named days pinned via
        # 'days' (or via 'day' when every session is pinned)
        for name, (sessions, want_days) in expected["session_days"].items():
            matches = [a for a in cfg.activities if name in a.name.lower()]
            assert len(matches) == 1, f"expected one '{name}' activity, got {matches}"
            act = matches[0]
            assert act.sessions_per_week == sessions
            pinned = set(act.days) | ({act.day} if act.day else set())
            for d in want_days:
                assert d in pinned, f"{name}: {d} not pinned (days={act.days}, day={act.day})"

    if "pinned_days" in expected:
        days = {a.day for a in cfg.activities if a.day}
        for day in expected["pinned_days"]:
            assert day in days, f"no activity pinned to {day}"
