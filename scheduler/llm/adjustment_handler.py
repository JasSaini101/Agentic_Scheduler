"""
Adjustment Handler
──────────────────
Applies natural language mid-week changes to an existing schedule.
Uses LangChain with ChatAnthropic + JsonOutputParser for structured action parsing.

Actions produced by the LLM:
  - remove: delete matching entry from schedule + Google Calendar
  - add:    find the best open slot for the activity via the relevant agent
"""

import os
import warnings

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from scheduler.config import UserConfig
from scheduler.models import ScheduleEntry

_SYSTEM_PROMPT = """\
You are a scheduling assistant. The user wants to make changes to their weekly schedule.
Parse their request into a JSON list of actions.

Each action must be one of:
  {{"action": "remove", "day": "DayName", "label": "Event label to remove"}}
  {{"action": "add", "activity": "activity name", "day": "DayName"}}

For "remove": match the event label as closely as possible from context.
For "add": use the activity name from the user's goals (e.g. "basketball", "gym", "tennis").
Day names are: Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday.

Return ONLY a JSON array of action objects, no other text.\
"""


def _build_chain():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set.")

    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_PROMPT),
        ("human", "Current schedule:\n{schedule_summary}\n\nRequested change: {change_text}"),
    ])

    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        temperature=0,
        api_key=api_key,
    )

    return (prompt | llm | JsonOutputParser()).with_retry(stop_after_attempt=2)


def _parse_actions(change_text: str, schedule: list[ScheduleEntry]) -> list[dict]:
    schedule_summary = "\n".join(
        f"- {e.day} {e.start_hour}:00 — {e.label} ({e.category})"
        for e in schedule
    )
    chain = _build_chain()
    return chain.invoke({"schedule_summary": schedule_summary, "change_text": change_text})


def _delete_calendar_event(entry: ScheduleEntry, calendar_id: str) -> None:
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = Credentials.from_authorized_user_file(
            "token.json",
            ["https://www.googleapis.com/auth/calendar"],
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

        service = build("calendar", "v3", credentials=creds)
        from scheduler.data.mock_week import DAYS, WEEK_START
        from datetime import timedelta

        idx = DAYS.index(entry.day)
        date_str = (WEEK_START + timedelta(days=idx)).isoformat()
        start_dt = f"{date_str}T{entry.start_hour:02d}:00:00Z"

        events = service.events().list(
            calendarId=calendar_id,
            timeMin=start_dt,
            maxResults=10,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        for event in events.get("items", []):
            if event.get("summary", "").lower() == entry.label.lower():
                private_props = event.get("extendedProperties", {}).get("private", {})
                if private_props.get("source") == "agentic_scheduler":
                    service.events().delete(calendarId=calendar_id, eventId=event["id"]).execute()
                    print(f"  [adjustment] Deleted calendar event: {entry.label} on {entry.day}")
                    return
    except BaseException as exc:
        warnings.warn(f"[adjustment] Could not delete calendar event for {entry.label}: {exc}")


def _add_activity(activity_name: str, day: str, schedule: list[ScheduleEntry], user_config: UserConfig) -> ScheduleEntry | None:
    from scheduler.week_context import build_week_context

    blocked = [
        {"day": e.day, "start_hour": e.start_hour, "end_hour": e.end_hour}
        for e in schedule
    ]
    week_context = {**build_week_context(), "blocked": blocked}

    target = next(
        (t for t in user_config.activities if t.name.lower() == activity_name.lower()),
        None,
    )

    if target is None:
        print(f"  [adjustment] No config found for activity '{activity_name}'; skipping add")
        return None

    if target.category == "gym":
        import scheduler.agents.gym_agent as gym_agent
        from scheduler.config import ActivityTarget, UserConfig as UC
        single = UC(
            activities=[ActivityTarget(
                name=target.name,
                sessions_per_week=1,
                duration_hours=target.duration_hours,
                candidate_hours=target.candidate_hours,
                category="gym",
            )],
            fill_window=user_config.fill_window,
            calendar_id=user_config.calendar_id,
        )
        day_blocked = [b for b in blocked if b["day"] != day]
        entries = gym_agent.run({**week_context, "blocked": day_blocked}, single)
        return next((e for e in entries if e.day == day), entries[0] if entries else None)

    if target.category == "sport":
        import scheduler.agents.sport_agent as sport_agent
        from scheduler.config import ActivityTarget, UserConfig as UC
        single = UC(
            activities=[ActivityTarget(
                name=target.name,
                sessions_per_week=1,
                duration_hours=target.duration_hours,
                candidate_hours=target.candidate_hours,
                category="sport",
            )],
            fill_window=user_config.fill_window,
            calendar_id=user_config.calendar_id,
        )
        day_blocked = [b for b in blocked if b["day"] != day]
        entries = sport_agent.run({**week_context, "blocked": day_blocked}, single)
        return next((e for e in entries if e.day == day), entries[0] if entries else None)

    print(f"  [adjustment] Add for category '{target.category}' not yet supported")
    return None


def apply(change_text: str, schedule: list[ScheduleEntry], user_config: UserConfig) -> list[ScheduleEntry]:
    """Parse the NL change, apply remove/add actions, update Google Calendar."""
    actions = _parse_actions(change_text, schedule)
    print(f"\n[adjustment] Applying {len(actions)} action(s): {actions}")

    result = list(schedule)

    for action in actions:
        if action["action"] == "remove":
            day = action.get("day", "")
            label = action.get("label", "")
            to_remove = [
                e for e in result
                if e.day == day and label.lower() in e.label.lower()
            ]
            for entry in to_remove:
                _delete_calendar_event(entry, user_config.calendar_id)
                result.remove(entry)
                print(f"  [adjustment] Removed: {entry.label} on {entry.day}")

        elif action["action"] == "add":
            activity = action.get("activity", "")
            day = action.get("day", "")
            new_entry = _add_activity(activity, day, result, user_config)
            if new_entry:
                result.append(new_entry)
                print(f"  [adjustment] Added: {new_entry.label} on {new_entry.day} at {new_entry.start_hour}:00")
                try:
                    from scheduler.data.providers.calendar_writer import write_schedule
                    write_schedule([new_entry], user_config.calendar_id)
                except BaseException as exc:
                    warnings.warn(f"[adjustment] Could not write new event to calendar: {exc}")

    return result
