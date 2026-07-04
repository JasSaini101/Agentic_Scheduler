"""
Core data models shared across all agents.
A TimeSlot is the atomic unit — one candidate block of time.
A ScheduleEntry is what ends up in the final weekly schedule.
"""

from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, field_validator


@dataclass
class TimeSlot:
    """A candidate block of time with a score and reasons."""
    day: str                        # e.g. "Monday"
    start_hour: int                 # 24h, e.g. 7
    duration_hours: float           # e.g. 1.5
    activity: str                   # e.g. "Gym session"
    score: float = 0.0              # 0.0 (worst) → 1.0 (best)
    reasons: list[str] = field(default_factory=list)
    blocked: bool = False           # True if a hard event overlaps

    @property
    def end_hour(self) -> float:
        return self.start_hour + self.duration_hours

    @property
    def label(self) -> str:
        end = int(self.end_hour)
        end_fmt = f"{end % 12 or 12}{'am' if end < 12 else 'pm'}"
        start_fmt = f"{self.start_hour % 12 or 12}{'am' if self.start_hour < 12 else 'pm'}"
        return f"{self.day} {start_fmt}–{end_fmt}"

    def overlaps(self, other_start: int, other_end: int) -> bool:
        """Check if this slot overlaps with a given hour range."""
        return not (self.end_hour <= other_start or self.start_hour >= other_end)


class ScheduleEntry(BaseModel):
    """A confirmed entry in the final weekly schedule."""
    day: str
    start_hour: int
    end_hour: float
    label: str
    category: str   # "gym" | "sport" | "hard_block" | "fill"
    score: Optional[float] = None
    reasons: list[str] = []

    @field_validator("start_hour", mode="before")
    @classmethod
    def coerce_start_hour(cls, v):
        return int(v)

    def __str__(self):
        end = int(self.end_hour)
        start_fmt = f"{self.start_hour % 12 or 12}{'am' if self.start_hour < 12 else 'pm'}"
        end_fmt = f"{end % 12 or 12}{'am' if end < 12 else 'pm'}"
        score_str = f"  [score: {self.score:.2f}]" if self.score else ""
        return f"  {self.day:10s} {start_fmt}–{end_fmt:6s} | {self.label}{score_str}"
