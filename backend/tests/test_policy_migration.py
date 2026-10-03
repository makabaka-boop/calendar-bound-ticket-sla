"""策略迁移: versions pinned; explicit migration shows old/new diff."""
from __future__ import annotations

from .conftest import CAL_247, create, scan, status


def test_new_policy_version_does_not_change_old_ticket(client):
    # Ticket pinned to v1 (warn 120 / L2 240).
    t = create(client, version=1, calendar=CAL_247,
               at="2026-10-03T00:00:00Z")
    # v2 already seeded (warn 60 / L2 180 / L3 480). Scanning at 90 min:
    # under v2 warning would already have fired, under v1 it must not.
    scan(client, "2026-10-03T01:30:00Z", ticket_id=t["id"])
    detail = client.get(f"/api/tickets/{t['id']}").json()
    assert detail["policy"]["version"] == 1
    assert detail["sla_events"] == []
    assert detail["accrued_work_minutes"] == 90


def test_preview_migration_reports_old_new_differences(client):
    t = create(client, version=1, calendar=CAL_247,
               at="2026-10-03T00:00:00Z")
    scan(client, "2026-10-03T01:30:00Z", ticket_id=t["id"])  # 90 minutes

    r = client.post(
        f"/api/tickets/{t['id']}/migrate/preview",
        json={"policy_key": "standard", "policy_version": 2,
              "at": "2026-10-03T01:30:00Z"})
    assert r.status_code == 200, r.text
    diff = r.json()
    assert diff["old_policy"]["version"] == 1
    assert diff["new_policy"]["version"] == 2
    assert diff["accrued_work_minutes"] == 90

    by_ref = {(x["kind"], x["ref"]): x for x in diff["thresholds"]}
    warn = by_ref[("warn", "warning")]
    assert warn["change"] == "tightened"
    assert warn["old_minutes"] == 120 and warn["new_minutes"] == 60
    assert warn["delta_minutes"] == -60
    assert warn["old_already_crossed"] is False
    assert warn["new_already_crossed"] is True
    assert warn["new_crossed_at"] == "2026-10-03T01:00:00.000Z"

    l2 = by_ref[("escalate", "L2")]
    assert l2["change"] == "tightened"
    l3 = by_ref[("escalate", "L3")]
    assert l3["change"] == "added"
    assert l3["old_minutes"] is None and l3["new_minutes"] == 480

    # Preview writes nothing.
    detail = client.get(f"/api/tickets/{t['id']}").json()
    assert detail["policy"]["version"] == 1
    assert detail["sla_events"] == []


def test_apply_migration_saves_evidence_and_backfills(client):
    t = create(client, version=1, calendar=CAL_247,
               at="2026-10-03T00:00:00Z")
    scan(client, "2026-10-03T03:30:00Z", ticket_id=t["id"])  # 210 minutes
    detail_before = client.get(f"/api/tickets/{t['id']}").json()
    rev = detail_before["revision"]
    # v1 warn (120) fired at 02:00; L2 (240) not yet.

    r = client.post(
        f"/api/tickets/{t['id']}/migrate",
        json={"policy_key": "standard", "policy_version": 2,
              "revision": rev, "reason": "contract change",
              "actor": "alice", "at": "2026-10-03T03:30:00Z"})
    assert r.status_code == 200, r.text
    body = r.json()
    mid = body["result"]["migration_id"]
    detail = body["ticket"]
    assert detail["policy"]["version"] == 2
    assert detail["revision"] == rev + 1

    # Old v1 warning retained as evidence, linked to migration; new version's
    # warn(60) + L2(180) backfilled at their real crossing instants.
    events = detail["sla_events"]
    v1warn = [e for e in events
              if e["policy_version"] == 1 and e["kind"] == "warn"]
    assert len(v1warn) == 1
    assert v1warn[0]["migration_id"] == mid
    # Original adjudication evidence is preserved untouched.
    assert v1warn[0]["basis"]["trigger"] == "timer"
    assert v1warn[0]["basis"]["policy_version"] == 1
    assert v1warn[0]["crossed_at"] == "2026-10-03T02:00:00.000Z"

    v2_warn = [e for e in events
               if e["policy_version"] == 2 and e["kind"] == "warn"]
    v2_l2 = [e for e in events
             if e["policy_version"] == 2 and e["ref"] == "L2"]
    assert len(v2_warn) == 1 and len(v2_l2) == 1
    assert v2_warn[0]["crossed_at"] == "2026-10-03T01:00:00.000Z"
    assert v2_l2[0]["crossed_at"] == "2026-10-03T03:00:00.000Z"
    assert v2_warn[0]["basis"]["trigger"] == "migration_backfill"

    # Migration record is retrievable with the full old/new diff.
    recs = client.get(f"/api/tickets/{t['id']}/migrations").json()
    assert len(recs) == 1
    assert recs[0]["id"] == mid
    assert recs[0]["revision_from"] == 1
    assert recs[0]["revision_to"] == 2
    assert recs[0]["diff"]["new_policy"]["version"] == 2
    assert recs[0]["reason"] == "contract change"

    # Post-migration timer scans use the new version without duplicates.
    scan(client, "2026-10-03T07:00:00Z", ticket_id=t["id"])
    detail2 = client.get(f"/api/tickets/{t['id']}").json()
    assert len([e for e in detail2["sla_events"]
                if e["policy_version"] == 2]) == 2  # warn + L2, L3 not yet
    # L3 (480) fires only after 480 accrued minutes, i.e. 08:00.
    scan(client, "2026-10-03T08:01:00Z", ticket_id=t["id"])
    detail3 = client.get(f"/api/tickets/{t['id']}").json()
    assert len([e for e in detail3["sla_events"]
                if e["policy_version"] == 2]) == 3
    l3 = [e for e in detail3["sla_events"]
          if e["policy_version"] == 2 and e["ref"] == "L3"]
    assert len(l3) == 1
    assert l3[0]["crossed_at"] == "2026-10-03T08:00:00.000Z"


def test_relaxed_migration_and_stale_revision_conflict(client):
    t = create(client, version=2, calendar=CAL_247,
               at="2026-10-03T00:00:00Z")
    # Relaxing 2 -> 1 needs the older version to exist (it does).
    r = client.post(
        f"/api/tickets/{t['id']}/migrate/preview",
        json={"policy_key": "standard", "policy_version": 1})
    assert r.status_code == 200
    by = {(x["kind"], x["ref"]): x for x in r.json()["thresholds"]}
    assert by[("warn", "warning")]["change"] == "relaxed"
    assert by[("warn", "warning")]["delta_minutes"] == 60
    assert by[("escalate", "L3")]["change"] == "removed"

    # stale revision -> 409
    r2 = client.post(
        f"/api/tickets/{t['id']}/migrate",
        json={"policy_key": "standard", "policy_version": 1,
              "revision": 999})
    assert r2.status_code == 409


def test_creating_new_policy_version_endpoint_pins_latest(client):
    r = client.post("/api/policies", json={
        "policy_key": "standard", "name": "Standard SLA",
        "spec": {"warn_minutes": 15,
                 "escalations": [{"level": "L2", "minutes": 30}]}})
    assert r.status_code == 201, r.text
    assert r.json()["version"] == 3
    t = create(client, calendar=CAL_247, at="2026-10-01T00:00:00Z")
    assert t["policy"]["version"] == 3
    scan(client, "2026-10-01T00:15:00Z", ticket_id=t["id"])
    detail = client.get(f"/api/tickets/{t['id']}").json()
    assert [e["kind"] for e in detail["sla_events"]] == ["warn"]
    scan(client, "2026-10-01T00:31:00Z", ticket_id=t["id"])
    detail = client.get(f"/api/tickets/{t['id']}").json()
    assert [(e["kind"], e["ref"]) for e in detail["sla_events"]] == [
        ("warn", "warning"), ("escalate", "L2")]
