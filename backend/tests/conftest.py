"""Shared pytest fixtures.

Tests use a fresh in-memory-ish file database per test and drive the API
through FastAPI's TestClient.  Endpoints accept an ``at`` override so the
entire scenario is deterministic without sleeping.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "..")))

from sla.api import create_app  # noqa: E402
from sla.seed import DEFAULT_CALENDAR  # noqa: E402
from sla.db import Database  # noqa: E402
from sla.seed import seed  # noqa: E402


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture()
def app(db_path):
    application = create_app(db_path, start_scanner=False, reseed=True)
    return application


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db(app):
    return app.state.db


CAL_247 = {
    "weekly_hours": [
        {"days": [1, 2, 3, 4, 5, 6, 7], "start": "00:00", "end": "24:00"}
    ],
    "work_intervals": [],
    "holidays": [],
}


def cal_with_holiday(start="2026-10-10T00:00:00Z",
                     end="2026-10-11T00:00:00Z"):
    return {
        "weekly_hours": [
            {"days": [1, 2, 3, 4, 5, 6, 7],
             "start": "00:00", "end": "24:00"}],
        "work_intervals": [],
        "holidays": [[start, end]],
    }


def create(client, title="T", version=None, calendar=None, at=None):
    payload = {"title": title, "calendar_spec": calendar or CAL_247}
    if version is not None:
        payload["policy_version"] = version
    if at is not None:
        payload["at"] = at
    r = client.post("/api/tickets", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def status(client, ticket_id, action, revision, at=None, note=""):
    payload = {"action": action, "revision": revision}
    if at is not None:
        payload["at"] = at
    if note:
        payload["note"] = note
    return client.post(f"/api/tickets/{ticket_id}/status", json=payload)


def scan(client, at, ticket_id=None):
    payload = {"at": at}
    if ticket_id is not None:
        payload["ticket_id"] = ticket_id
    return client.post("/api/admin/scan", json=payload)
