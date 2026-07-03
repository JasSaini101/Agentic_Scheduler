"""
Pipeline
────────
Top-level LangChain chain: parse goals → build schedule.
Returns (schedule, user_config) so the caller can run mid-week adjustments.
The adjustment step stays outside the chain (requires interactive user input mid-flow).
"""

from langchain_core.runnables import RunnableLambda

from scheduler.llm.goal_parser import parse_goals
from scheduler import orchestrator


def _parse_and_run(goals: str):
    user_config = parse_goals(goals)
    print(f"[main] Parsed {len(user_config.activities)} activities:")
    for a in user_config.activities:
        print(f"  - {a.name} ({a.category}): {a.sessions_per_week}x/week × {a.duration_hours}h")
    schedule = orchestrator.run(user_config)
    return schedule, user_config


# goals (str) → (list[ScheduleEntry], UserConfig)
pipeline = RunnableLambda(_parse_and_run)
