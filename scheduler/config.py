from dataclasses import dataclass, field


@dataclass
class ActivityTarget:
    name: str
    sessions_per_week: int
    duration_hours: float
    candidate_hours: list[int]
    category: str   # "gym" | "sport" | "fill"


@dataclass
class UserConfig:
    activities: list[ActivityTarget]
    fill_window: tuple[int, int] = (8, 22)
    calendar_id: str = "primary"
    raw_goals: str = ""

    def by_category(self, category: str) -> list[ActivityTarget]:
        return [a for a in self.activities if a.category == category]
