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
    pinned_day: str | None = None     # hard pin to one day (all sessions)
    pinned_days: list[str] = Field(default_factory=list)  # per-session pins: session i → pinned_days[i]
    allowed_hours: list[int] = Field(default_factory=list)  # candidate starts; empty → whole window
    max_chunk_hours: float = 4.0
    min_chunk_hours: float = 1.0
    one_per_day: bool = True          # at most one block of this activity per day

    @model_validator(mode="after")
    def _check(self):
        if self.pinned_day is not None and self.pinned_day not in DAYS:
            raise ValueError(f"pinned_day must be one of {DAYS}, got {self.pinned_day!r}")
        for d in self.pinned_days:
            if d not in DAYS:
                raise ValueError(f"pinned_days entries must be one of {DAYS}, got {d!r}")
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
    """An edit to a ConstraintSet, re-solved after applying. Used by the
    critic's revision loop and by user change requests ("add two gym
    sessions at 12 or later", "tennis on back to back days")."""
    add_requests: list[ActivityRequest] = Field(default_factory=list)
    add_exclusions: list[HardExclusion] = Field(default_factory=list)
    add_preferences: list[SoftPreference] = Field(default_factory=list)
    pin_activity: dict[str, str] = Field(default_factory=dict)   # activity name → day
    # activity name → field overrides on its ActivityRequest, e.g.
    # {"gym": {"sessions": 4, "allowed_hours": [12, 13, ..., 21]}}
    # {"tennis": {"pinned_days": ["Saturday", "Sunday"]}}
    update_requests: dict[str, dict] = Field(default_factory=dict)
    remove_activities: list[str] = Field(default_factory=list)
    rationale: str = ""

    def apply(self, cs: ConstraintSet) -> ConstraintSet:
        updates = {k.lower(): v for k, v in self.update_requests.items()}
        pins = {k.lower(): v for k, v in self.pin_activity.items()}
        removals = {n.lower() for n in self.remove_activities}
        requests = []
        for req in cs.requests:
            key = req.name.lower()
            if key in removals:
                continue
            if key in updates:
                req = req.model_copy(update=updates[key])
            if key in pins:
                req = req.model_copy(update={"pinned_day": pins[key]})
            requests.append(req)
        return ConstraintSet(
            requests=requests + self.add_requests,
            exclusions=cs.exclusions + self.add_exclusions,
            preferences=cs.preferences + self.add_preferences,
            fill_window=cs.fill_window,
        )
