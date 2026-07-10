"""
Goal Parser
───────────
Converts a natural language weekly goals string into a UserConfig object.
Uses LangChain with ChatAnthropic + PydanticOutputParser for structured,
validated output — no manual JSON parsing or markdown-fence stripping needed.
"""

import os
from functools import lru_cache

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel

from scheduler.config import ActivityTarget, UserConfig

_DEFAULT_CANDIDATE_HOURS = {
    "gym":   [6, 7, 17, 18],
    "sport": [8, 9, 10, 16, 17],
    "fill":  list(range(8, 22)),
}

_SYSTEM_TEMPLATE = """\
You are a scheduling assistant. Parse the user's weekly fitness and productivity goals into structured data.

{format_instructions}

Rules:
- "gym" category: anything at a gym (weights, cardio machines, CrossFit, etc.)
- "sport" category: outdoor or recreational sports (tennis, basketball, soccer, swimming, etc.)
- "fill" category: work, study, personal projects, hobbies, errands, anything else
- For hour-quota fill activities like "20h personal projects", set sessions_per_week=1 and duration_hours=20.0
- For session-based activities like "gym 3x", set sessions_per_week=3 and duration_hours=1.5
- Do not include candidate_hours — leave it as an empty list
- fill_window should always be [8, 22] unless the user specifies otherwise\
"""


class _ParsedActivity(BaseModel):
    name: str
    sessions_per_week: int
    duration_hours: float
    category: str


class _ParsedConfig(BaseModel):
    activities: list[_ParsedActivity]
    fill_window: list[int] = [8, 22]


@lru_cache(maxsize=1)
def _build_chain():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Goal parsing requires the Anthropic API. "
            "Add it to your .env file."
        )

    parser = PydanticOutputParser(pydantic_object=_ParsedConfig)

    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_TEMPLATE),
        ("human", "{goals}"),
    ]).partial(format_instructions=parser.get_format_instructions())

    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        temperature=0,
        api_key=api_key,
    )

    return (prompt | llm | parser).with_retry(stop_after_attempt=2)


def parse_goals(goals: str, calendar_id: str = "primary") -> UserConfig:
    """Parse a natural language goals string into a UserConfig."""
    chain = _build_chain()
    parsed: _ParsedConfig = chain.invoke({"goals": goals})

    activities = [
        ActivityTarget(
            name=item.name,
            sessions_per_week=item.sessions_per_week,
            duration_hours=item.duration_hours,
            candidate_hours=_DEFAULT_CANDIDATE_HOURS.get(item.category, _DEFAULT_CANDIDATE_HOURS["fill"]),
            category=item.category,
        )
        for item in parsed.activities
    ]

    return UserConfig(
        activities=activities,
        fill_window=tuple(parsed.fill_window),
        calendar_id=calendar_id,
        raw_goals=goals,
    )
