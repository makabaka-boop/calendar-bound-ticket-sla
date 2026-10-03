"""到期与解决竞争: threshold fires exactly once; resolve vs timer ordering."""
from __future__ import annotations

import threading

from .conftest import CAL_247, create, scan, status


def test_warn_and_escalation_registered_once_across_repeated_scans(client):
    t = create(client, version=1, calendar=CAL_247,
               at="2026-10-03T00:00:00Z")
    # v1: warn 120, escalate L2 240.
    for ts in ("2026-10-03T01:00:00Z", "2026-10-03T03:00:00Z",
               "2026-10-03T05:00:00Z"):
        r = scan(client, ts, ticket_id=t["id"])
        assert r.status_code == 200

    # Simulate "restart": brand-new client/app hitting the same DB file is
    # unnecessary at domain level (nothing is cached), but repeat the scans
    # with the same client and even earlier timestamps.
    for ts in ("2026-10-03T03:30:00Z", "2026-10-03T10:00:00Z"):
        scan(client, ts, ticket_id=t["id"])

    detail = client.get(f"/api/tickets/{t['id']}").json()
    kinds = [(e["kind"], e["ref"]) for e in detail["sla_events"]]
    assert kinds.count(("warn", "warning")) == 1
    assert kinds.count(("escalate", "L2")) == 1
    warn = next(e for e in detail["sla_events"] if e["kind"] == "warn")
    esc = next(e for e in detail["sla_events"]
               if e["kind"] == "escalate")
    assert warn["crossed_at"] == "2026-10-03T02:00:00.000Z"
    assert esc["crossed_at"] == "2026-10-03T04:00:00.000Z"
    # Evidence is retained on each event.
    assert warn["basis"]["accrued_work_minutes"] >= 120
    assert warn["basis"]["policy_version"] == 1
    assert esc["basis"]["threshold_minutes"] == 240


def test_resolved_ticket_is_not_scanned_and_keeps_evidence(client):
    t = create(client, version=1, calendar=CAL_247,
               at="2026-10-03T00:00:00Z")
    # Resolve at 90 minutes: nothing has crossed yet.
    status(client, t["id"], "resolve", 1,
           at="2026-10-03T01:30:00Z")
    r = scan(client, "2026-10-03T12:00:00Z")  # full sweep, much later
    assert r.status_code == 200
    assert r.json()["registered"] == []
    detail = client.get(f"/api/tickets/{t['id']}").json()
    assert detail["status"] == "resolved"
    assert detail["accrued_work_minutes"] == 90
    assert detail["sla_events"] == []


def test_resolve_after_threshold_keeps_single_event(client):
    t = create(client, version=1, calendar=CAL_247,
               at="2026-10-03T00:00:00Z")
    scan(client, "2026-10-03T02:30:00Z", ticket_id=t["id"])
    status(client, t["id"], "resolve", 1,
           at="2026-10-03T02:45:00Z")
    scan(client, "2026-10-04T00:00:00Z")
    detail = client.get(f"/api/tickets/{t['id']}").json()
    assert len(detail["sla_events"]) == 1
    assert detail["sla_events"][0]["kind"] == "warn"


def test_timer_vs_user_action_serialized_no_duplicate_no_lost_event(
        app, client):
    """Resolve and timer scan fire concurrently.

    Whichever commits first, the invariants hold:
      * either the warning exists exactly once and ticket resolves after it,
        or resolve wins the race and no warning ever registers;
      * never two warnings, never a warning after resolve was the committed
        order while accrued says otherwise.
    """
    from sla.adjudicate import scan_due
    from sla.store import apply_status

    db = app.state.db
    t = create(client, version=1, calendar=CAL_247,
               at="2026-10-03T00:00:00Z")
    tid = t["id"]

    from sla.clock import parse_ts
    at = parse_ts("2026-10-03T02:30:00Z")  # 150 min -> warning overdue

    barrier = threading.Barrier(2)
    outcomes = []

    def do_scan():
        barrier.wait()
        scan_due(db, at=at)
        outcomes.append("scan")

    def do_resolve():
        barrier.wait()
        try:
            apply_status(db, tid, "resolve", expected_revision=1, at=at)
            outcomes.append("resolved")
        except Exception as exc:  # noqa: BLE001
            outcomes.append(("error", repr(exc)))

    th = [threading.Thread(target=do_scan),
          threading.Thread(target=do_resolve)]
    for x in th:
        x.start()
    for x in th:
        x.join()

    detail = client.get(f"/api/tickets/{tid}").json()
    warns = [e for e in detail["sla_events"] if e["kind"] == "warn"]
    assert len(warns) <= 1
    if detail["status"] == "resolved":
        # Resolved: scan afterward must never add events for this ticket.
        r = scan(client, "2026-10-10T00:00:00Z", ticket_id=tid)
        detail2 = client.get(f"/api/tickets/{tid}").json()
        after = [e for e in detail2["sla_events"] if e["kind"] == "warn"]
        assert len(after) == len(warns)
    else:
        assert len(warns) == 1


def test_backdated_ticket_adjudicates_on_first_scan(client):
    # Created "in the past": a later scan finds warn+escalation already due.
    t = create(client, version=1, calendar=CAL_247,
               at="2020-01-01T00:00:00Z")
    r = scan(client, "2020-01-01T12:00:00Z", ticket_id=t["id"])
    assert r.status_code == 200
    assert len(r.json()["registered"]) == 2
    detail = client.get(f"/api/tickets/{t['id']}").json()
    kinds = [e["kind"] for e in detail["sla_events"]]
    assert "warn" in kinds
    assert "escalate" in kinds
