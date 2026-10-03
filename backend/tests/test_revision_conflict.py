"""过期修订提交返回冲突: optimistic revision control."""
from __future__ import annotations

from .conftest import CAL_247, create, status


def test_stale_status_submit_returns_409(client):
    t = create(client, calendar=CAL_247, at="2026-10-03T08:00:00Z")
    # First pause succeeds with revision 1.
    r = status(client, t["id"], "pause", 1,
               at="2026-10-03T09:00:00Z")
    assert r.status_code == 200
    # A second client still holding revision 1 tries to pause -> 409.
    r = status(client, t["id"], "pause", 1,
               at="2026-10-03T09:05:00Z")
    assert r.status_code == 409
    body = r.json()
    assert "冲突" in body["detail"]
    # Nothing was appended: still revision 2, still 2 state events.
    detail = client.get(f"/api/tickets/{t['id']}").json()
    assert detail["revision"] == 2
    assert len(detail["state_events"]) == 2


def test_current_revision_succeeds_after_refresh(client):
    t = create(client, calendar=CAL_247, at="2026-10-03T08:00:00Z")
    status(client, t["id"], "pause", 1, at="2026-10-03T09:00:00Z")
    detail = client.get(f"/api/tickets/{t['id']}").json()
    r = status(client, t["id"], "resume", detail["revision"],
               at="2026-10-03T10:00:00Z")
    assert r.status_code == 200
    assert r.json()["revision"] == 3


def test_revision_increments_on_resolve_reopen(client):
    t = create(client, calendar=CAL_247, at="2026-10-03T08:00:00Z")
    assert t["revision"] == 1
    r = status(client, t["id"], "resolve", 1,
               at="2026-10-03T09:00:00Z")
    assert r.json()["revision"] == 2
    r = status(client, t["id"], "reopen", 2,
               at="2026-10-03T10:00:00Z")
    assert r.json()["revision"] == 3


def test_404_for_missing_ticket(client):
    r = client.post("/api/tickets/9999/status",
                    json={"action": "pause", "revision": 1})
    assert r.status_code == 404
