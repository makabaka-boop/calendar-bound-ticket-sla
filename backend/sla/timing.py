"""Ticket timing model.

Everything is derived from the append-only per-ticket state-event log:

* start events: ``open`` / ``resume`` / ``reopen`` — the clock runs
* stop events:  ``pause`` / ``resolve`` — the clock stops (waiting on the
  customer, or finished)

Pairing starts with the next stop yields the *active* (counted) intervals;
the gaps are the *pause* intervals.  Active intervals accumulate *effective
working minutes* only inside the pinned working calendar (holidays cut out).
Reopening a resolved ticket simply adds a new active interval; previously
accrued minutes are preserved because the whole log is replayed.

A scanner adjudges the pinned policy thresholds against these derived
intervals; threshold crossing instants are pure functions of the event log
and the calendar, so restarts and repeated scans always reach the same
answer and each warning/escalation registers at most once.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

from .calendar import Calendar
from .clock import format_ts
from .intervals import (
    Interval,
    closed_intervals,
    cut_at,
    intersect,
    subtract,
    total_minutes,
    union,
)

START_KINDS = {"open", "resume", "reopen"}
STOP_KINDS = {"pause", "resolve"}


@dataclass
class StateEvent:
    seq: int
    kind: str
    at: datetime
    note: str = ""


def _pairs(events: Sequence[StateEvent], at: datetime,
           ) -> Tuple[List[Interval], List[Interval],
                      Optional[datetime], Optional[datetime]]:
    """Return (active_intervals, pause_intervals, open_since, open_pause).

    Intervals are cut at ``at``.  ``open_since`` is set when the clock is
    currently running (the last event is a start); ``open_pause`` is set
    when the ticket is currently waiting on the customer (last event is a
    pause), so the UI can render the still-open pause interval.
    """
    ev = sorted(events, key=lambda e: (e.at, e.seq))
    if not ev:
        return [], [], None, None
    starts = [e.at for e in ev if e.kind in START_KINDS]
    stops = [e.at for e in ev if e.kind in STOP_KINDS]
    t0 = starts[0]

    running = ev[-1].kind in START_KINDS
    # Pair a running clock with a sentinel stop at ``at`` so the open
    # segment participates in the same interval math.
    paired_stops = stops + ([at] if running else [])
    active = cut_at(closed_intervals(starts, paired_stops), at)

    timeline_end = at if running else stops[-1]
    timeline_end = min(timeline_end, at)
    pauses = (subtract([(t0, timeline_end)], active)
              if timeline_end > t0 else [])

    open_since = ev[-1].at if running else None
    open_pause = (ev[-1].at
                  if (not running and ev[-1].kind == "pause") else None)
    return active, pauses, open_since, open_pause


def working_slices(calendar: Calendar, active: Sequence[Interval],
                   ) -> List[Interval]:
    """Effective working-time intervals: active time inside the calendar."""
    out: List[Interval] = []
    for s, e in active:
        out = union(out, calendar.materialize(s, e))
    # materialize is already within [s,e); intersect defensively
    return intersect(out, list(active))


def accrued_minutes(calendar: Calendar, events: Sequence[StateEvent],
                    at: datetime) -> Tuple[float, List[Interval],
                                           List[Interval], Optional[datetime],
                                           Optional[datetime]]:
    """Accumulated effective working minutes at time ``at``.

    Returns (minutes, active_intervals, pause_intervals, open_since,
    open_pause).
    """
    active, pauses, open_since, open_pause = _pairs(events, at)
    slices = working_slices(calendar, active)
    return (total_minutes(slices), active, pauses, open_since, open_pause)


def crossing_instant(calendar: Calendar, active: Sequence[Interval],
                     budget_minutes: float) -> Optional[datetime]:
    """UTC instant at which the active intervals accrued ``budget_minutes``.

    Looks only at calendar materialized up to the end of ``active`` (i.e.
    history plus an open segment's current bound).  Returns ``None`` if the
    budget has not been reached.
    """
    if not active or budget_minutes < 0:
        return None
    budget = timedelta(minutes=budget_minutes)
    acc = timedelta()
    for s, e in working_slices(calendar, active):
        seg = e - s
        if acc + seg >= budget:
            return s + (budget - acc)
        acc += seg
    return None


def project_deadline(calendar: Calendar, events: Sequence[StateEvent],
                     budget_minutes: float, at: datetime,
                     horizon_days: int = 3650,
                     ) -> Optional[datetime]:
    """Project the future deadline for ``budget_minutes``.

    Assumes the clock keeps running (no further pauses).  Returns:

    * an instant in the past/future if the budget can be satisfied,
    * ``None`` while the ticket is currently paused (deadline suspended) or
      after it was resolved.
    """
    active, _pauses, open_since, _open_pause = _pairs(events, at)
    if open_since is None:
        return None
    slices = working_slices(calendar, active)
    acc = timedelta()
    budget = timedelta(minutes=budget_minutes)
    for s, e in slices:
        seg = e - s
        if acc + seg >= budget:
            return s + (budget - acc)
        acc += seg
    # Budget not yet reached: extend the running segment into the future and
    # walk the calendar weekly windows.
    remaining = budget - acc
    future_end = at + timedelta(days=horizon_days)
    future_work = calendar.materialize(at, future_end)
    for s, e in future_work:
        seg = e - s
        if seg >= remaining:
            return s + remaining
        remaining -= seg
    return None  # calendar does not offer enough working time in horizon


def intervals_json(ivs: Sequence[Interval]) -> list:
    return [[format_ts(s), format_ts(e)] for s, e in ivs]
