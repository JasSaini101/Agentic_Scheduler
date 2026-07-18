"""
CP-SAT scheduling core (Google OR-Tools).

Consumes a ConstraintSet and solves the whole week globally on a 30-minute
grid. Hard guarantees for everything that IS placed:
  - no overlaps with other blocks or hard exclusions
  - day pins honored
  - at most one session per activity per day
  - inside the fill window / allowed start hours

Blocks are OPTIONAL with a placement reward that dominates the objective:
the solver places as much of the request as physically fits and reports the
shortfall in SolveResult.unplaced instead of failing the whole schedule
(and instead of the old greedy behavior of dropping sessions silently).

Objective hierarchy (per 30-min slot / unit):
  placement (50) ≫ agent preferences (±5) > quota spread (6/day) > balance (2)

InfeasibleError is reserved for structurally impossible requests (a block
larger than the daily window).
"""

import math
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from scheduler.data.mock_week import DAYS
from scheduler.models import ScheduleEntry
from scheduler.solver.constraints import ActivityRequest, ConstraintSet

SLOTS_PER_HOUR = 2          # 30-minute grid
SLOTS_PER_DAY = 24 * SLOTS_PER_HOUR
PLACE_WEIGHT = 50           # reward per slot actually scheduled (dominates)
SPREAD_WEIGHT = 6           # bonus per distinct day a quota activity uses
BALANCE_WEIGHT = 2          # pressure to shrink the busiest day


class InfeasibleError(Exception):
    def __init__(self, message: str, culprits: list[str] | None = None):
        super().__init__(message)
        self.culprits = culprits or []


@dataclass
class SolveResult:
    entries: list[ScheduleEntry]
    unplaced: list[str] = field(default_factory=list)   # human-readable shortfalls


def _to_slots(hours: float) -> int:
    return int(round(hours * SLOTS_PER_HOUR))


def _chunk_hours(req: ActivityRequest) -> list[float]:
    """Expand a request into fixed block durations (hours, 0.5 grid)."""
    if req.total_hours is None:
        return [req.session_hours] * req.sessions
    total = req.total_hours
    n = min(7, max(1, math.ceil(total / req.max_chunk_hours)))
    base = math.floor((total / n) * 2) / 2 or 0.5
    chunks = [base] * n
    remainder = round((total - base * n) * 2)
    for i in range(remainder):
        chunks[i % n] += 0.5
    return [c for c in chunks if c > 0]


def _merge_duplicates(requests: list[ActivityRequest]) -> list[ActivityRequest]:
    """Merge session-based requests for the same activity into one.

    The parser may split "3 tennis, one on Monday and one on Friday" into two
    'tennis' activities. Left separate, one-per-day would not hold across
    them and any whole-request pin would contradict itself. Merging sums the
    sessions, turns pins into per-session pinned_days, and makes shortfall
    reports count the user's real total."""
    out: list[ActivityRequest] = []
    index: dict[tuple, int] = {}
    for req in requests:
        key = (req.name.lower(), req.category, req.session_hours)
        if req.total_hours is not None or key not in index:
            if req.total_hours is None:
                index[key] = len(out)
            out.append(req)
            continue
        base = out[index[key]]
        pins = ([base.pinned_day] if base.pinned_day else []) + base.pinned_days \
             + ([req.pinned_day] if req.pinned_day else []) + req.pinned_days
        out[index[key]] = base.model_copy(update={
            "sessions": base.sessions + req.sessions,
            "pinned_day": None,
            "pinned_days": pins,
            "allowed_hours": sorted(set(base.allowed_hours) | set(req.allowed_hours)),
        })
    return out


def _label(req: ActivityRequest) -> str:
    return "Gym session" if req.name.lower() == "gym" else req.name.title()


def solve(cs: ConstraintSet, time_limit_s: float = 10.0) -> SolveResult:
    cs = cs.model_copy(update={"requests": _merge_duplicates(cs.requests)})
    model = cp_model.CpModel()
    win_lo = _to_slots(cs.fill_window[0])
    win_hi = _to_slots(cs.fill_window[1])

    # block: (req, dur_slots, day, offset, interval, onehot, present)
    blocks = []

    for req in cs.requests:
        for ci, dur_h in enumerate(_chunk_hours(req)):
            dur = _to_slots(dur_h)
            lo, hi = win_lo, win_hi - dur
            if req.allowed_hours:
                starts = [h * SLOTS_PER_HOUR for h in req.allowed_hours
                          if win_lo <= h * SLOTS_PER_HOUR <= win_hi - dur]
                if not starts:
                    raise InfeasibleError(
                        f"'{req.name}': no allowed start hour fits a {dur_h}h "
                        f"block inside the {cs.fill_window} window", [req.name])
                offset = model.NewIntVarFromDomain(
                    cp_model.Domain.FromValues(starts), f"off_{len(blocks)}")
            else:
                if hi < lo:
                    raise InfeasibleError(
                        f"'{req.name}': a {dur_h}h block does not fit inside "
                        f"the {cs.fill_window} window", [req.name])
                offset = model.NewIntVar(lo, hi, f"off_{len(blocks)}")

            day = model.NewIntVar(0, 6, f"day_{len(blocks)}")
            if req.pinned_day:
                model.Add(day == DAYS.index(req.pinned_day))
            elif ci < len(req.pinned_days):
                model.Add(day == DAYS.index(req.pinned_days[ci]))

            onehot = [model.NewBoolVar(f"d{d}_{len(blocks)}") for d in range(7)]
            model.AddExactlyOne(onehot)
            model.Add(day == sum(d * onehot[d] for d in range(7)))

            start = model.NewIntVar(0, 7 * SLOTS_PER_DAY, f"start_{len(blocks)}")
            model.Add(start == day * SLOTS_PER_DAY + offset)
            present = model.NewBoolVar(f"p_{len(blocks)}")
            interval = model.NewOptionalFixedSizeIntervalVar(
                start, dur, present, f"iv_{len(blocks)}")
            blocks.append((req, dur, day, offset, interval, onehot, present))

    # One session per activity per day, among the blocks actually placed
    by_request: dict[int, list] = {}
    for b in blocks:
        by_request.setdefault(id(b[0]), []).append(b)
    spread_terms = []
    for req_blocks in by_request.values():
        req = req_blocks[0][0]
        if len(req_blocks) <= 1:
            continue
        if req.total_hours is not None:
            # quota: spread across distinct days is a strong bonus, not a wall
            for d in range(7):
                used = model.NewBoolVar(f"used_{id(req)}_{d}")
                day_hits = []
                for b in req_blocks:
                    h = model.NewBoolVar(f"uh_{id(b)}_{d}")
                    model.AddBoolAnd([b[5][d], b[6]]).OnlyEnforceIf(h)
                    model.AddBoolOr([b[5][d].Not(), b[6].Not()]).OnlyEnforceIf(h.Not())
                    day_hits.append(h)
                model.AddMaxEquality(used, day_hits)
                spread_terms.append(SPREAD_WEIGHT * used)
        elif req.one_per_day:
            for i, a in enumerate(req_blocks):
                for b in req_blocks[i + 1:]:
                    model.Add(a[2] != b[2]).OnlyEnforceIf([a[6], b[6]])

    # Hard exclusions. Global ones are merged into disjoint ranges per day
    # first — the user's own calendar can contain nested events (a 1:1 inside
    # a Work block), and overlapping fixed intervals would make NoOverlap
    # unsatisfiable on its own. Activity-scoped exclusions forbid overlap for
    # matching blocks only.
    merged: dict[int, list[tuple[int, int]]] = {}
    for ex in cs.exclusions:
        d = DAYS.index(ex.day)
        s, e = _to_slots(ex.start_hour), _to_slots(ex.end_hour)
        if e <= s:
            continue
        if ex.activity is None:
            merged.setdefault(d, []).append((s, e))
        else:
            ex_start = d * SLOTS_PER_DAY + s
            ex_end = d * SLOTS_PER_DAY + e
            for req, dur, day, offset, interval, _, present in blocks:
                if req.name.lower() != ex.activity.lower():
                    continue
                before = model.NewBoolVar("exb")
                model.Add(interval.StartExpr() + dur <= ex_start).OnlyEnforceIf([before, present])
                model.Add(interval.StartExpr() >= ex_end).OnlyEnforceIf([before.Not(), present])

    fixed_intervals = []
    for d, ranges in merged.items():
        ranges.sort()
        disjoint: list[list[int]] = []
        for s, e in ranges:
            if disjoint and s <= disjoint[-1][1]:
                disjoint[-1][1] = max(disjoint[-1][1], e)
            else:
                disjoint.append([s, e])
        for s, e in disjoint:
            fixed_intervals.append(model.NewFixedSizeIntervalVar(
                d * SLOTS_PER_DAY + s, e - s, f"ex_{len(fixed_intervals)}"))

    model.AddNoOverlap([b[4] for b in blocks] + fixed_intervals)

    # ── Objective ───────────────────────────────────────────────────────────
    objective_terms = []

    # placement reward: schedule as many requested slots as physically fit
    for req, dur, day, offset, interval, onehot, present in blocks:
        objective_terms.append(PLACE_WEIGHT * dur * present)

    # agent preferences (count only for placed blocks)
    for bi, (req, dur, day, offset, interval, onehot, present) in enumerate(blocks):
        for pi, pref in enumerate(cs.preferences):
            if pref.activity is not None and pref.activity.lower() != req.name.lower():
                continue
            if pref.weight == 0:
                continue
            conds = [present]
            if pref.day is not None:
                conds.append(onehot[DAYS.index(pref.day)])
            lo_s, hi_s = _to_slots(pref.hour_start), _to_slots(pref.hour_end)
            if lo_s > win_lo:
                ge = model.NewBoolVar(f"ge_{bi}_{pi}")
                model.Add(offset >= lo_s).OnlyEnforceIf(ge)
                model.Add(offset < lo_s).OnlyEnforceIf(ge.Not())
                conds.append(ge)
            if hi_s < win_hi:
                lt = model.NewBoolVar(f"lt_{bi}_{pi}")
                model.Add(offset < hi_s).OnlyEnforceIf(lt)
                model.Add(offset >= hi_s).OnlyEnforceIf(lt.Not())
                conds.append(lt)
            hit = model.NewBoolVar(f"hit_{bi}_{pi}")
            model.AddBoolAnd(conds).OnlyEnforceIf(hit)
            model.AddBoolOr([c.Not() for c in conds]).OnlyEnforceIf(hit.Not())
            objective_terms.append(pref.weight * hit)

    # balance: shrink the busiest day (placed blocks only)
    day_loads = []
    for d in range(7):
        terms = []
        for req, dur, day, offset, interval, onehot, present in blocks:
            pd = model.NewBoolVar(f"pd_{d}_{len(terms)}_{id(req)}")
            model.AddBoolAnd([onehot[d], present]).OnlyEnforceIf(pd)
            model.AddBoolOr([onehot[d].Not(), present.Not()]).OnlyEnforceIf(pd.Not())
            terms.append(dur * pd)
        load = model.NewIntVar(0, 7 * SLOTS_PER_DAY, f"load_{d}")
        model.Add(load == sum(terms))
        day_loads.append(load)
    max_load = model.NewIntVar(0, 7 * SLOTS_PER_DAY, "max_load")
    model.AddMaxEquality(max_load, day_loads)
    objective_terms.append(-BALANCE_WEIGHT * max_load)

    model.Maximize(sum(objective_terms) + sum(spread_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # With optional blocks this only happens on contradictory hard
        # constraints (e.g. pinned into an exclusion) — surface it honestly.
        raise InfeasibleError(
            "No feasible schedule — conflicting hard constraints "
            f"(status: {solver.StatusName(status)})")

    return _extract(solver, blocks, cs)


def _extract(solver, blocks, cs: ConstraintSet) -> SolveResult:
    entries = []
    placed_hours: dict[int, float] = {}
    for req, dur, day, offset, interval, _, present in blocks:
        if not solver.Value(present):
            continue
        placed_hours[id(req)] = placed_hours.get(id(req), 0) + dur / SLOTS_PER_HOUR
        d = DAYS[solver.Value(day)]
        start_h = solver.Value(offset) / SLOTS_PER_HOUR
        end_h = start_h + dur / SLOTS_PER_HOUR
        reasons = [p.reason for p in cs.preferences
                   if p.reason
                   and (p.activity is None or p.activity.lower() == req.name.lower())
                   and (p.day is None or p.day == d)
                   and p.hour_start <= start_h < p.hour_end]
        if req.pinned_day:
            reasons.insert(0, f"Pinned to {req.pinned_day}")
        entries.append(ScheduleEntry(
            day=d, start_hour=start_h, end_hour=end_h,
            label=_label(req), category=req.category, reasons=reasons,
        ))

    unplaced = []
    for req in cs.requests:
        wanted = req.total_hours if req.total_hours is not None \
            else req.sessions * req.session_hours
        got = placed_hours.get(id(req), 0.0)
        if got + 0.01 < wanted:
            if req.total_hours is not None:
                unplaced.append(
                    f"{req.name}: only {got:.1f}h of {wanted:.0f}h fit in the week")
            else:
                n_got = round(got / req.session_hours) if req.session_hours else 0
                unplaced.append(
                    f"{req.name}: only {n_got} of {req.sessions} session(s) fit "
                    "(free days/hours ran out)")

    entries.sort(key=lambda e: (DAYS.index(e.day), e.start_hour))
    return SolveResult(entries=entries, unplaced=unplaced)
