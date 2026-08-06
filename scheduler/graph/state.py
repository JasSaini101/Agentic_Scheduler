"""
Shared state passed between agent nodes in the scheduling graph.
"""

from typing import Annotated, TypedDict

from scheduler.config import UserConfig
from scheduler.models import ScheduleEntry


def _append(left: list, right: list) -> list:
    return left + right


class ScheduleState(TypedDict, total=False):
    goals: str                      # raw natural-language goals
    user_email: str | None          # calendar owner (multi-user web); None = legacy
    user_config: UserConfig | None  # parsed config (None until parse node runs)
    week_context: dict              # weather / sleep / busyness / hard events
    constraints: object             # ConstraintSet accumulated by agent nodes
    entries: list[ScheduleEntry]    # hard blocks + solver output
    infeasible: str | None          # solver's infeasibility explanation
    agent_log: Annotated[list[str], _append]   # rationale trail across nodes
    done_agents: list[str]          # supervisor bookkeeping
    critic_feedback: str | None     # critic's revision request, if any
    revisions_left: int             # remaining critic→solve loops
