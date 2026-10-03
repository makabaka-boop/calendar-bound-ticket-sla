"""Seed data: demo calendar + policy versions.

Calendar: 24x7 working time with a fixed UTC holiday interval as the
"假日切口" demo.  Policy ``standard`` starts at v1 (warn 120 work-minutes,
escalate L2 240) and has a v2 (warn 60, L2 180, L3 480) so migration
old-vs-new differences are visible.
"""
from __future__ import annotations

from .clock import format_ts, now_utc
from .db import Database
from .store import create_policy_version, get_or_create_calendar

DEFAULT_CALENDAR = {
    "weekly_hours": [
        {"days": [1, 2, 3, 4, 5, 6, 7], "start": "00:00", "end": "24:00"},
    ],
    "work_intervals": [],
    # A fixed holiday window (UTC). No working minutes accrue inside it.
    "holidays": [
        ["2026-10-10T00:00:00Z", "2026-10-11T00:00:00Z"],
    ],
}

POLICY_V1 = {
    "warn_minutes": 120,
    "escalations": [
        {"level": "L2", "minutes": 240, "action": "notify-manager"},
    ],
}
POLICY_V2 = {
    "warn_minutes": 60,
    "escalations": [
        {"level": "L2", "minutes": 180, "action": "notify-manager"},
        {"level": "L3", "minutes": 480, "action": "page-director"},
    ],
}


def seed(db: Database) -> dict:
    p1 = create_policy_version(db, "standard", POLICY_V1,
                               name="Standard SLA", description="v1 seed")
    p2 = create_policy_version(db, "standard", POLICY_V2,
                               name="Standard SLA", description="v2 seed")
    cal_id, calendar = get_or_create_calendar(db, DEFAULT_CALENDAR)
    return {"policies": [p1, p2], "calendar_id": cal_id,
            "calendar_spec": DEFAULT_CALENDAR}
