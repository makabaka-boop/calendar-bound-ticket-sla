"""Interval set arithmetic on half-open UTC intervals [start, end).

Intervals are tuples of aware ``datetime`` objects (UTC).  Every public
function returns *normalized* interval lists: sorted, non-overlapping,
touching intervals merged, zero-length intervals dropped.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Sequence, Tuple

Interval = Tuple[datetime, datetime]


def _norm(ivs: Sequence[Interval]) -> List[Interval]:
    out: List[Interval] = []
    for s, e in sorted((s, e) for s, e in ivs if e > s):
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def union(a: Sequence[Interval], b: Sequence[Interval]) -> List[Interval]:
    """Union of two interval sets."""
    return _norm(list(a) + list(b))


def intersect(a: Sequence[Interval], b: Sequence[Interval]) -> List[Interval]:
    """Intersection of two interval sets."""
    out: List[Interval] = []
    i = j = 0
    la, lb = list(a), list(b)
    while i < len(la) and j < len(lb):
        s = max(la[i][0], lb[j][0])
        e = min(la[i][1], lb[j][1])
        if e > s:
            out.append((s, e))
        if la[i][1] <= lb[j][1]:
            i += 1
        else:
            j += 1
    return _norm(out)


def subtract(a: Sequence[Interval], b: Sequence[Interval]) -> List[Interval]:
    """Set difference ``a - b`` of two interval sets."""
    out: List[Interval] = list(a)
    for bs, be in b:
        nxt: List[Interval] = []
        for s, e in out:
            if be <= s or bs >= e:
                nxt.append((s, e))
                continue
            if bs > s:
                nxt.append((s, bs))
            if be < e:
                nxt.append((be, e))
        out = nxt
    return _norm(out)


def closed_intervals(
    opens: Sequence[datetime], closes: Sequence[datetime]
) -> List[Interval]:
    """Pair open/close events into intervals.

    Events are interleaved: an open starts (or extends) an interval, the
    next close ends it.  Unmatched opens are left open-ended only when the
    caller supplies ``datetime.max`` as a sentinel close; this function
    instead leaves a trailing open as ``(last_open, None)`` — callers that
    need a bound use :func:`bound_open_interval`.
    """
    events = [(t, 1) for t in opens] + [(t, -1) for t in closes]
    events.sort(key=lambda x: (x[0], -x[1]))  # opens before closes at tie
    out: List[Interval] = []
    depth = 0
    start = None
    for t, delta in events:
        if depth == 0 and delta > 0:
            start = t
        depth += delta
        if depth == 0 and start is not None:
            if t > start:
                out.append((start, t))
            start = None
    return _norm(out)


def total_minutes(ivs: Sequence[Interval]) -> float:
    """Total length of the interval set, in minutes."""
    return sum((e - s).total_seconds() for s, e in ivs) / 60.0


def bound_open(ivs: Sequence[Interval], horizon: datetime) -> List[Interval]:
    """Clamp every interval so it ends no later than ``horizon``."""
    out: List[Interval] = []
    for s, e in ivs:
        if s >= horizon:
            continue
        out.append((s, min(e, horizon)))
    return _norm(out)


def cut_at(ivs: Sequence[Interval], at: datetime) -> List[Interval]:
    """Truncate the interval set at time ``at`` (future bits removed)."""
    return bound_open(ivs, at)


def minutes_until(ivs: Sequence[Interval], at: datetime) -> float:
    return total_minutes(cut_at(ivs, at))


def add_work_minutes(
    calendar_intervals: Sequence[Interval],
    start: datetime,
    minutes: float,
) -> datetime:
    """Project a deadline: the UTC instant at which ``minutes`` of working
    time have elapsed since ``start``.

    Working time is the given calendar intervals (already intersected with
    the ticket's lifetime).  If the available working time is exhausted
    before reaching the target, returns the end of the last working
    interval; callers distinguish this via :func:`project_deadline`.
    """
    budget = timedelta(minutes=minutes)
    acc = timedelta()
    for s, e in calendar_intervals:
        if e <= start:
            continue
        seg_s = max(s, start)
        seg_len = e - seg_s
        if acc + seg_len >= budget:
            return seg_s + (budget - acc)
        acc += seg_len
    raise ValueError("not enough working time to satisfy the budget")


def has_enough(calendar_intervals: Sequence[Interval], start: datetime,
               minutes: float) -> bool:
    budget = timedelta(minutes=minutes)
    acc = timedelta()
    for s, e in calendar_intervals:
        if e <= start:
            continue
        acc += e - max(s, start)
        if acc >= budget:
            return True
    return False
