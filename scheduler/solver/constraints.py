"""
Constraint vocabulary — the sole interface between the LLM agent layer and
the CP-SAT solver.

Agents emit these Pydantic objects into graph state; the solver consumes a
ConstraintSet and returns a schedule. The LLM layer never places events
directly, and the solver never sees natural language.
"""

from pydantic import BaseModel, Field, model_validator

from scheduler.data.mock_week import DAYS


class HardExclusion(BaseModel):
    """A time range nothing may be scheduled into (existing calendar events,
    storm days for outdoor sports, etc.)."""
    day: str
    start_hour: float
    end_hour: float
    reason: str = ""
    activity: str | None = None   # None → applies to every activity


class ActivityRequest(BaseModel):
    """One activity that must appear on the schedule.

    Session-based:  sessions × session_hours blocks (e.g. gym 3× 1.5h).
    Quota-based:    total_hours split into chunks ≤ max_chunk_hours
                    (e.g. 20h personal projects)."""
    name: str
    category: str                     # "gym" | "sport" | "fill"
    sessions: int = 1
    session_hours: float = 1.0
    total_hours: float | None = None  # set → quota-based
    pinned_day: str | None = None     # hard pin to one day
    allowed_hours: list[int] = Field(default_factory=list)  # candidate starts; empty → whole window
    max_chunk_hours: float = 4.0
    min_chunk_hours: float = 1.0
    one_per_day: bool = True          # at most one block of this activity per day

    @model_validator(mode="after")
    def _check(self):
        if self.pinned_day is not None and self.pinned_day not in DAYS:
            raise ValueError(f"pinned_day must be one of {DAYS}, got {self.pinned_day!r}")
        return self


class SoftPreference(BaseModel):
    """A weighted nudge on the objective — never blocks a feasible schedule."""
    activity: str | None = None       # None → applies to every activity
    day: str | None = None            # None → every day
    hour_start: float = 0.0
    hour_end: float = 24.0
    weight: int = 0                   # >0 bonus, <0 penalty (objective units)
    reason: str = ""


class ConstraintSet(BaseModel):
    """Everything the solver needs, accumulated by the agent nodes."""
    requests: list[ActivityRequest] = Field(default_factory=list)
    exclusions: list[HardExclusion] = Field(default_factory=list)
    preferences: list[SoftPreference] = Field(default_factory=list)
    fill_window: tuple[float, float] = (8.0, 22.0)

    def merged(self, other: "ConstraintSet") -> "ConstraintSet":
        return ConstraintSet(
            requests=self.requests + other.requests,
            exclusions=self.exclusions + other.exclusions,
            preferences=self.preferences + other.preferences,
            fill_window=other.fill_window if other.fill_window != (8.0, 22.0) else self.fill_window,
        )


class ConstraintPatch(BaseModel):
    """Critic feedback expressed as an edit to the ConstraintSet — the
    revision loop re-solves after applying it, instead of hand-editing
    the schedule."""
    add_exclusions: list[HardExclusion] = Field(default_factory=list)
    add_preferences: list[SoftPreference] = Field(default_factory=list)
    pin_activity: dict[str, str] = Field(default_factory=dict)   # activity name → day
    rationale: str = ""

    def apply(self, cs: ConstraintSet) -> ConstraintSet:
        requests = []
        for req in cs.requests:
            if req.name in self.pin_activity:
                req = req.model_copy(update={"pinned_day": self.pin_activity[req.name]})
            requests.append(req)
        return ConstraintSet(
            requests=requests,
            exclusions=cs.exclusions + self.add_exclusions,
            preferences=cs.preferences + self.add_preferences,
            fill_window=cs.fill_window,
        )
