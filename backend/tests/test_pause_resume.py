"""暂停跨界: waiting on customer pauses; resume/reopen keep old minutes."""
from __future__ import annotations

from .conftest import CAL_247, create, scan, status


def test_pause_stops_accrual_and_pause_interval_shown(client):
    t = create(client, version=1, calendar=CAL_247,
               at="2026-10-03T08:00:00Z")
    r = status(client, t["id"], "pause", revision=1,
               at="2026-10-03T09:00:00Z", note="waiting for customer")
    assert r.status_code == 200, r.text
    # A long wall-clock period while paused...
    scan(client, "2026-10-05T12:00:00Z", ticket_id=t["id"])
    detail = client.get(f"/api/tickets/{t['id']}").json()
    assert detail["status"] == "paused"
    assert detail["accrued_work_minutes"] == 60
    # Open-ended pause interval rendered up to "now".
    assert detail["pause_intervals"] == [
        ["2026-10-03T09:00:00.000Z", "2026-10-05T12:00:00.000Z"]]
    assert detail["open_pause_since"] == "2026-10-03T09:00:00.000Z"
    # Deadline is suspended while paused.
    assert all(d["suspended"] for d in detail["deadlines"])


def test_resume_continues_accruing_existing_minutes(client):
    t = create(client, version=1, calendar=CAL_247,
               at="2026-10-03T08:00:00Z")
    status(client, t["id"], "pause", 1, at="2026-10-03T09:00:00Z")
    # resume requires the NEW revision (2)
    r = status(client, t["id"], "resume", 2,
               at="2026-10-03T13:00:00Z")
    assert r.status_code == 200, r.text
    scan(client, "2026-10-03T15:00:00Z", ticket_id=t["id"])
    detail = client.get(f"/api/tickets/{t['id']}").json()
    # 60 (before pause) + 120 (13:00-15:00) = 180 -> warn (120) fired
    assert detail["accrued_work_minutes"] == 180
    warn = [e for e in detail["sla_events"] if e["kind"] == "warn"]
    assert len(warn) == 1
    # warn crossing = resume 13:00 + 60 min = 14:00
    assert warn[0]["crossed_at"] == "2026-10-03T14:00:00.000Z"
    # Two counted intervals, one closed pause interval between them.
    assert len(detail["counted_intervals"]) == 2
    assert detail["pause_intervals"] == [
        ["2026-10-03T09:00:00.000Z", "2026-10-03T13:00:00.000Z"]]


def test_pause_crossing_holiday_boundary(client):
    # Holiday 10:00-12:00. Pause 11:00-13:00 straddles the holiday end.
    cal = {
        "weekly_hours": [
            {"days": [1, 2, 3, 4, 5, 6, 7],
             "start": "00:00", "end": "24:00"}],
        "work_intervals": [],
        "holidays": [["2026-10-03T10:00:00Z",
                      "2026-10-03T12:00:00Z"]],
    }
    t = create(client, version=1, calendar=cal,
               at="2026-10-03T08:00:00Z")
    status(client, t["id"], "pause", 1, at="2026-10-03T11:00:00Z")
    status(client, t["id"], "resume", 2, at="2026-10-03T13:00:00Z")
    scan(client, "2026-10-03T14:00:00Z", ticket_id=t["id"])
    detail = client.get(f"/api/tickets/{t['id']}").json()
    # active: 8-11 (holiday 10-11 removes 60 -> 120) + 13-14 (60) = 180
    assert detail["accrued_work_minutes"] == 180
    # Pause window as recorded, independent of holiday.
    assert detail["pause_intervals"] == [
        ["2026-10-03T11:00:00.000Z", "2026-10-03T13:00:00.000Z"]]


def test_reopen_keeps_accumulated_minutes(client):
    t = create(client, version=1, calendar=CAL_247,
               at="2026-10-03T08:00:00Z")
    status(client, t["id"], "resolve", 1, at="2026-10-03T10:00:00Z")
    detail0 = client.get(f"/api/tickets/{t['id']}").json()
    assert detail0["status"] == "resolved"
    # reopen requires revision 2
    r = status(client, t["id"], "reopen", 2,
               at="2026-10-04T08:00:00Z", note="regression")
    assert r.status_code == 200, r.text
    scan(client, "2026-10-04T09:00:00Z", ticket_id=t["id"])
    detail = client.get(f"/api/tickets/{t['id']}").json()
    # old 120 min preserved + new 60 = 180
    assert detail["accrued_work_minutes"] == 180
    assert len(detail["counted_intervals"]) == 2


def test_illegal_transitions_rejected(client):
    t = create(client, calendar=CAL_247, at="2026-10-03T08:00:00Z")
    # cannot resolve while paused
    status(client, t["id"], "pause", 1, at="2026-10-03T09:00:00Z")
    r = status(client, t["id"], "resolve", 2,
               at="2026-10-03T09:30:00Z")
    assert r.status_code == 409
    # cannot reopen an open ticket
    r = status(client, t["id"], "reopen", 2,
               at="2026-10-03T09:30:00Z")
    assert r.status_code == 409
