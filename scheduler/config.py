from pydantic import BaseModel


class ActivityTarget(BaseModel):
    name: str
    sessions_per_week: int
    duration_hours: float
    candidate_hours: list[int] = []
    category: str   # "gym" | "sport" | "fill"
    day: str | None = None   # pin the whole activity to one day (e.g. "Friday")
    days: list[str] = []     # per-session pins: session i → days[i]; rest free


class UserConfig(BaseModel):
    activities: list[ActivityTarget]
    fill_window: tuple[int, int] = (8, 22)
    calendar_id: str = "primary"
    raw_goals: str = ""

    def by_category(self, category: str) -> list[ActivityTarget]:
        return [a for a in self.activities if a.category == category]
