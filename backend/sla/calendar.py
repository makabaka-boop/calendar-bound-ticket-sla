"""Working calendar.

A calendar spec (JSON-serializable) describes:

* ``weekly_hours``: recurring weekly work windows.  Day-of-week uses the
  ISO numbering (Mon=1..Sun=7), times are ``HH:MM`` in UTC.
* ``work_intervals``: extra *ad-hoc* working windows given as explicit UTC
  ``[start, end)`` pairs.
* ``holidays``: UTC ``[start, end)`` cutouts — working time inside a holiday
  is removed ("假日切口").

The working calendar is therefore defined purely by UTC intervals; weekly
hours are just compact notation for repeating intervals and let deadlines
project arbitrarily far into the future.

Calendar specs are versioned and pinned on each ticket at creation time, so
editing the calendar never silently rewrites the adjudication basis of an
existing ticket.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence

from .clock import parse_ts
from .intervals import Interval, subtract, union

WEEK_MINUTES = 7 * 24 * 60


@dataclass
class WindowRule:
    days: List[int]                      # ISO weekdays 1..7
    start_minute: int                    # minutes from 00:00 UTC
    end_minute: int                      # minutes from 00:00 UTC, may exceed 1440

    def on_day(self, day: datetime) -> Optional[Interval]:
        if day.isoweekday() not in self.days:
            return None
        midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        s = midnight + timedelta(minutes=self.start_minute)
        e = midnight + timedelta(minutes=self.end_minute)
        if e <= s:
            return None
        return s, e


@dataclass
class Calendar:
    weekly_hours: List[WindowRule] = field(default_factory=list)
    work_intervals: List[Interval] = field(default_factory=list)
    holidays: List[Interval] = field(default_factory=list)

    # ------------------------------------------------------------------ spec
    @classmethod
    def from_spec(cls, raw: str | dict) -> "Calendar":
        spec = json.loads(raw) if isinstance(raw, str) else raw
        weekly: List[WindowRule] = []
        for rule in spec.get("weekly_hours", []):
            weekly.append(
                WindowRule(
                    days=list(rule["days"]),
                    start_minute=_hm(rule["start"]),
                    end_minute=_hm(rule.get("end", rule.get("end_minute"))),
                )
            )
        extra = [
            (parse_ts(a), parse_ts(b))
            for a, b in spec.get("work_intervals", [])
        ]
        holidays = [
            (parse_ts(a), parse_ts(b))
            for a, b in spec.get("holidays", [])
        ]
        return cls(weekly_hours=weekly,
                   work_intervals=union(extra, []),
                   holidays=union(holidays, []))

    def to_spec(self) -> dict:
        return {
            "weekly_hours": [
                {
                    "days": r.days,
                    "start": _mh(r.start_minute),
                    "end": _mh(r.end_minute),
                }
                for r in self.weekly_hours
            ],
            "work_intervals": [[_iso(s), _iso(e)] for s, e in self.work_intervals],
            "holidays": [[_iso(s), _iso(e)] for s, e in self.holidays],
        }

    # ------------------------------------------------------------- material
    def materialize(self, start: datetime, end: datetime) -> List[Interval]:
        """Working intervals within the half-open range ``[start, end)``.

        Weekly windows plus ad-hoc work intervals, with holiday cutouts
        removed and everything clamped to the requested range.
        """
        if end <= start:
            return []
        # Day loop; a window can spill past midnight (end_minute > 1440), so
        # seed from the day before the range start.
        day = (start - timedelta(days=1)).astimezone(timezone.utc)
        last_day = end.astimezone(timezone.utc)
        windows: List[Interval] = []
        while day.date() <= last_day.date():
            for rule in self.weekly_hours:
                iv = rule.on_day(day)
                if iv is not None:
                    s, e = iv
                    if e > start and s < end:
                        windows.append((max(s, start), min(e, end)))
            day += timedelta(days=1)
        for s, e in self.work_intervals:
            if e > start and s < end:
                windows.append((max(s, start), min(e, end)))
        covered = union(windows, [])
        cut_holidays = [
            (max(s, start), min(e, end))
            for s, e in self.holidays
            if e > start and s < end
        ]
        return subtract(covered, cut_holidays)


def _hm(value: str | int) -> int:
    if isinstance(value, int):
        return value
    h, m = value.split(":")
    h, m = int(h), int(m)
    # "24:00" denotes end-of-day (1440), not minute 24.
    return h * 60 + m


def _mh(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
