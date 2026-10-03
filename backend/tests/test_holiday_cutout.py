"""假日切口: holiday UTC intervals must remove working time."""
from __future__ import annotations

from .conftest import CAL_247, create, scan


def test_holiday_cutout_does_not_accrue(client):
    # Ticket runs 23:00 -> next day 05:00 across a 2h holiday (00:00-02:00).
    cal = {
        "weekly_hours": [
            {"days": [1, 2, 3, 4, 5, 6, 7],
             "start": "00:00", "end": "24:00"}],
        "work_intervals": [],
        "holidays": [["2026-10-04T00:00:00Z", "2026-10-04T02:00:00Z"]],
    }
    t = create(client, calendar=cal, at="2026-10-03T23:00:00Z")
    r = scan(client, "2026-10-04T05:00:00Z", ticket_id=t["id"])
    assert r.status_code == 200, r.text

    detail = client.get(f"/api/tickets/{t['id']}").json()
    # 6h wall time - 2h holiday = 4h = 240 minutes
    assert detail["accrued_work_minutes"] == 240

    # Working slices skip the holiday: last slice starts at 02:00.
    slices = detail["working_slices"]
    assert slices == [
        ["2026-10-03T23:00:00.000Z", "2026-10-04T00:00:00.000Z"],
        ["2026-10-04T02:00:00.000Z", "2026-10-04T05:00:00.000Z"],
    ]


def test_holiday_inside_otherwise_due_window_delays_warning(client):
    # v1 warn threshold is 120 work minutes.
    cal = {
        "weekly_hours": [
            {"days": [1, 2, 3, 4, 5, 6, 7],
             "start": "00:00", "end": "24:00"}],
        "work_intervals": [],
        "holidays": [["2026-10-10T00:00:00Z", "2026-10-10T02:00:00Z"]],
    }
    t = create(client, version=1, calendar=cal,
               at="2026-10-09T23:00:00Z")
    # At 01:00 wall clock 2h passed, but only 1h working -> no warning.
    scan(client, "2026-10-10T01:00:00Z", ticket_id=t["id"])
    detail = client.get(f"/api/tickets/{t['id']}").json()
    assert detail["accrued_work_minutes"] == 60
    assert [e for e in detail["sla_events"] if e["kind"] == "warn"] == []

    # At 03:00 -> 60 (before) + 60 (after holiday) = 120 -> warning.
    scan(client, "2026-10-10T03:00:00Z", ticket_id=t["id"])
    detail = client.get(f"/api/tickets/{t['id']}").json()
    warn = [e for e in detail["sla_events"] if e["kind"] == "warn"]
    assert len(warn) == 1
    assert warn[0]["crossed_at"] == "2026-10-10T03:00:00.000Z"
    # Basis records the holiday-cut slices.
    assert len(warn[0]["basis"]["working_slices"]) == 2


def test_calendar_without_holiday_accrues_wall_time(client):
    t = create(client, calendar=CAL_247, at="2026-10-03T08:00:00Z")
    scan(client, "2026-10-03T10:00:00Z", ticket_id=t["id"])
    detail = client.get(f"/api/tickets/{t['id']}").json()
    assert detail["accrued_work_minutes"] == 120
    assert len(detail["working_slices"]) == 1
