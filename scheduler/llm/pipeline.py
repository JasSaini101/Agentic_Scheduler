"""
Pipeline
────────
Top-level entry: parse goals → run the LangGraph agent graph → (optionally)
write to Google Calendar. Returns (schedule, user_config, calendar_error) so
callers can run mid-week adjustments and surface sync failures.
"""

from langchain_core.runnables import RunnableLambda

from scheduler.graph.build import run_schedule


def _parse_and_run(payload):
    # Accepts a plain goals string (CLI — writes calendar immediately) or a
    # {"goals": str, "write_calendar": bool} dict (web preview/confirm flow).
    if isinstance(payload, str):
        goals, write_calendar, user_email = payload, True, None
    else:
        goals = payload["goals"]
        write_calendar = payload.get("write_calendar", True)
        user_email = payload.get("user_email")

    schedule, user_config, agent_log, constraints = run_schedule(goals, user_email)
    for line in agent_log:
        print(f"[graph] {line}")

    calendar_error: str | None = None
    if write_calendar:
        try:
            from scheduler.data.providers.calendar_writer import write_schedule
            write_schedule(schedule, user_config.calendar_id, user_email=user_email)
        except Exception as exc:
            calendar_error = f"Schedule built, but writing to Google Calendar failed: {exc}"
    return schedule, user_config, calendar_error, constraints


# goals (str | dict) →
#   (list[ScheduleEntry], UserConfig, calendar_error | None, ConstraintSet)
pipeline = RunnableLambda(_parse_and_run)
