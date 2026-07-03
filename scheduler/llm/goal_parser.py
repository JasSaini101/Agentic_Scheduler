"""
Goal Parser
───────────
Converts a natural language weekly goals string into a UserConfig object.
Uses claude-haiku-4-5-20251001 at temperature 0 for deterministic JSON output.
Raises ValueError on parse failure — no silent fallback (ANTHROPIC_API_KEY required).
"""

import json
import os
import anthropic

from scheduler.config import ActivityTarget, UserConfig

_DEFAULT_CANDIDATE_HOURS = {
    "gym":   [6, 7, 17, 18],
    "sport": [8, 9, 10, 16, 17],
    "fill":  [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],
}

_SYSTEM_PROMPT = """
You are a scheduling assistant. Parse the user's weekly fitness and productivity goals into JSON.

Output ONLY valid JSON — no prose, no markdown fences. The schema:
{
  "activities": [
    {
      "name": string,          // e.g. "gym", "tennis", "basketball", "personal project"
      "sessions_per_week": int, // number of sessions per week; for hour-quota activities set to 1
      "duration_hours": float,  // hours per session; for "20h personal projects" set to 20.0 with sessions_per_week=1
      "category": "gym" | "sport" | "fill"
    }
  ],
  "fill_window": [8, 22]       // always [8, 22] unless user specifies otherwise
}

Rules:
- "gym" category: anything at a gym (weights, cardio machines, CrossFit, etc.)
- "sport" category: outdoor or recreational sports (tennis, basketball, soccer, swimming, etc.)
- "fill" category: work, study, personal projects, hobbies, errands, anything else
- For hour-quota fill activities like "20h personal projects", set sessions_per_week=1 and duration_hours=20.0
- For session-based activities like "gym 3x", set sessions_per_week=3 and a reasonable duration (gym=1.5h, sport=1.5h)
- Do not output candidate_hours — that is added by the parser
""".strip()


def parse_goals(goals: str, calendar_id: str = "primary") -> UserConfig:
    """Parse a natural language goals string into a UserConfig."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Goal parsing requires the Anthropic API. "
            "Add it to your .env file."
        )

    client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        temperature=0,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": goals}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0].strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Goal parser returned invalid JSON.\n"
            f"Raw output: {raw!r}\n"
            f"Please rephrase your goals more clearly.\n"
            f"Original error: {exc}"
        )

    activities: list[ActivityTarget] = []
    for item in data.get("activities", []):
        category = item.get("category", "fill")
        activities.append(ActivityTarget(
            name=item["name"],
            sessions_per_week=int(item["sessions_per_week"]),
            duration_hours=float(item["duration_hours"]),
            candidate_hours=_DEFAULT_CANDIDATE_HOURS.get(category, _DEFAULT_CANDIDATE_HOURS["fill"]),
            category=category,
        ))

    fill_window = tuple(data.get("fill_window", [8, 22]))

    return UserConfig(
        activities=activities,
        fill_window=fill_window,
        calendar_id=calendar_id,
        raw_goals=goals,
    )
