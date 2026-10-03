"""Repository functions: policies, calendars, tickets, state transitions."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from .calendar import Calendar
from .clock import format_ts, now_utc, parse_ts
from .db import Database, spec_hash
from .policy import Policy
from .timing import StateEvent

VALID_TRANSITIONS = {
    "open":   {"pause", "resolve"},
    "paused": {"resume"},
    "resolved": {"reopen"},
}


class StaleRevision(Exception):
    """Client submitted an outdated revision -> HTTP 409."""


class IllegalTransition(Exception):
    pass


# ------------------------------------------------------------ policy/calendar
def get_or_create_calendar(db: Database, spec: dict,
                           tx=None) -> tuple[int, Calendar]:
    own = tx is None
    tx = tx or db.write_tx()
    try:
        h = spec_hash(spec)
        row = tx.query_one(
            "SELECT id FROM calendars WHERE spec_hash = ?", (h,))
        if row is None:
            cur = tx.execute(
                "INSERT INTO calendars(spec, spec_hash, created_at) "
                "VALUES (?,?,?)",
                (json.dumps(spec, ensure_ascii=False, sort_keys=True),
                 h, format_ts(now_utc())))
            cal_id = cur.lastrowid
        else:
            cal_id = row["id"]
        if own:
            tx.__exit__(None, None, None)
        return cal_id, Calendar.from_spec(spec)
    except Exception:
        if own:
            tx.__exit__(True, None, None)
        raise


def load_calendar(tx, calendar_id: int) -> Calendar:
    row = tx.query_one("SELECT spec FROM calendars WHERE id = ?",
                       (calendar_id,))
    return Calendar.from_spec(row["spec"])


def create_policy_version(db: Database, policy_key: str, spec: dict,
                          name: str, description: str = "",
                          tx=None) -> Policy:
    """Insert a new immutable policy version; same spec reuses the version."""
    own = tx is None
    tx = tx or db.write_tx()
    try:
        h = spec_hash(spec)
        existing = tx.query_one(
            "SELECT id, version FROM policies WHERE policy_key = ? "
            "AND spec_hash = ?", (policy_key, h))
        if existing is not None:
            row = tx.query_one("SELECT * FROM policies WHERE id = ?",
                               (existing["id"],))
            policy = _row_to_policy(row)
        else:
            top = tx.query_one(
                "SELECT MAX(version) AS v FROM policies WHERE policy_key = ?",
                (policy_key,))
            version = (top["v"] or 0) + 1
            tx.execute(
                "INSERT INTO policies(policy_key, version, name, spec, "
                "spec_hash, description, created_at) VALUES (?,?,?,?,?,?,?)",
                (policy_key, version, name,
                 json.dumps(spec, ensure_ascii=False, sort_keys=True),
                 h, description, format_ts(now_utc())))
            row = tx.query_one(
                "SELECT * FROM policies WHERE policy_key = ? AND version = ?",
                (policy_key, version))
            policy = _row_to_policy(row)
        if own:
            tx.__exit__(None, None, None)
        return policy
    except Exception:
        if own:
            tx.__exit__(True, None, None)
        raise


def get_policy_by_version(tx, policy_key: str, version: int) -> Policy:
    row = tx.query_one(
        "SELECT * FROM policies WHERE policy_key = ? AND version = ?",
        (policy_key, version))
    if row is None:
        raise KeyError(f"policy {policy_key} v{version} not found")
    return _row_to_policy(row)


def get_policy_by_id(tx, policy_id: int) -> Policy:
    row = tx.query_one("SELECT * FROM policies WHERE id = ?", (policy_id,))
    return _row_to_policy(row)


def latest_policy(tx, policy_key: str) -> Optional[Policy]:
    row = tx.query_one(
        "SELECT * FROM policies WHERE policy_key = ? ORDER BY version DESC "
        "LIMIT 1", (policy_key,))
    return _row_to_policy(row) if row else None


def list_policies(tx, policy_key: Optional[str] = None) -> List[Policy]:
    if policy_key:
        rows = tx.query_all(
            "SELECT * FROM policies WHERE policy_key = ? ORDER BY version",
            (policy_key,))
    else:
        rows = tx.query_all(
            "SELECT * FROM policies ORDER BY policy_key, version")
    return [_row_to_policy(r) for r in rows]


def _row_to_policy(row) -> Policy:
    return Policy.from_row(
        key=row["policy_key"], version=row["version"], name=row["name"],
        spec=row["spec"], description=row["description"])


# ------------------------------------------------------------------- tickets
@dataclass
class TicketBundle:
    ticket: dict
    policy: Policy
    calendar: Calendar
    events: List[StateEvent]


def create_ticket(db: Database, title: str, policy_key: str,
                  policy_version: Optional[int], calendar_spec: dict,
                  at: datetime, note: str = "", actor: str = "") -> dict:
    """Pin policy version + calendar version at creation in one tx."""
    with db.write_tx() as tx:
        if policy_version is None:
            policy = latest_policy(tx, policy_key)
            if policy is None:
                raise KeyError(f"no policy with key {policy_key}")
        else:
            policy = get_policy_by_version(tx, policy_key, policy_version)
        cal_id, _ = get_or_create_calendar(db, calendar_spec, tx=tx)
        now = format_ts(at)
        cur = tx.execute(
            "INSERT INTO tickets(title, status, policy_id, policy_version, "
            "calendar_id, created_at, updated_at, revision) "
            "VALUES (?,?,?,?,?,?,?,1)",
            (title, "open", _policy_row_id(tx, policy), policy.version,
             cal_id, now, now))
        ticket_id = cur.lastrowid
        _append_event(tx, ticket_id, "open", at, note)
        _log_op(tx, "create", ticket_id, at, actor)
        row = tx.query_one("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        return dict(row)


def _policy_row_id(tx, policy: Policy) -> int:
    row = tx.query_one(
        "SELECT id FROM policies WHERE policy_key = ? AND version = ?",
        (policy.key, policy.version))
    return row["id"]


def load_bundle(tx, ticket_id: int) -> TicketBundle:
    row = tx.query_one("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    if row is None:
        raise KeyError(f"ticket {ticket_id} not found")
    policy = get_policy_by_id(tx, row["policy_id"])
    calendar = load_calendar(tx, row["calendar_id"])
    events = [
        StateEvent(seq=r["seq"], kind=r["kind"], at=parse_ts(r["at"]),
                   note=r["note"])
        for r in tx.query_all(
            "SELECT * FROM ticket_events WHERE ticket_id = ? ORDER BY seq",
            (ticket_id,))
    ]
    return TicketBundle(ticket=dict(row), policy=policy,
                        calendar=calendar, events=events)


def _append_event(tx, ticket_id: int, kind: str, at: datetime,
                  note: str) -> int:
    row = tx.query_one(
        "SELECT MAX(seq) AS s FROM ticket_events WHERE ticket_id = ?",
        (ticket_id,))
    seq = (row["s"] or 0) + 1
    tx.execute(
        "INSERT INTO ticket_events(ticket_id, seq, kind, at, note) "
        "VALUES (?,?,?,?,?)",
        (ticket_id, seq, kind, format_ts(at), note))
    return seq


def apply_status(db: Database, ticket_id: int, action: str,
                 expected_revision: int, at: datetime,
                 note: str = "", actor: str = "") -> dict:
    """Run a user status action.

    Same ``BEGIN IMMEDIATE`` queue as the timer, so the user action and any
    concurrent scan commit in a single serial order.  ``expected_revision``
    is the revision the client read; a mismatch raises StaleRevision (409).
    """
    with db.write_tx() as tx:
        row = tx.query_one("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        if row is None:
            raise KeyError(f"ticket {ticket_id} not found")
        if row["revision"] != expected_revision:
            raise StaleRevision(
                f"expected revision {expected_revision}, "
                f"current {row['revision']}")
        status = row["status"]
        if action not in VALID_TRANSITIONS.get(status, set()):
            raise IllegalTransition(
                f"cannot {action} a ticket in status {status}")
        _append_event(tx, ticket_id, action, at, note)
        new_status = {"pause": "paused", "resume": "open",
                      "resolve": "resolved", "reopen": "open"}[action]
        tx.execute(
            "UPDATE tickets SET status = ?, revision = revision + 1, "
            "updated_at = ? WHERE id = ?",
            (new_status, format_ts(at), ticket_id))
        # Snapshot accrued minutes in the same serial transaction so the
        # displayed counted-time basis is correct even without a later scan.
        from .adjudicate import snapshot_accrued
        snapshot_accrued(db, ticket_id, at, tx=tx)
        _log_op(tx, action, ticket_id, at, actor)
        out = tx.query_one("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        return dict(out)


def _log_op(tx, op: str, ticket_id: Optional[int], at: datetime,
            detail: str = "") -> None:
    tx.execute(
        "INSERT INTO ops_log(op, ticket_id, at, detail) VALUES (?,?,?,?)",
        (op, ticket_id, format_ts(at), detail))
