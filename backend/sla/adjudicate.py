"""Adjudication: timer scans and explicit policy migration.

Both paths serialize through ``BEGIN IMMEDIATE`` transactions (see
``sla.db``), so the timer and user actions always commit in one global
order.  Warning/escalation events carry the pinned policy version and are
protected by a unique constraint — restarts and repeat scans register each
one at most once.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from .clock import format_ts, now_utc
from .db import Database
from .policy import Policy
from .store import (
    StaleRevision,
    _log_op,
    _policy_row_id,
    get_policy_by_version,
    load_bundle,
)
from .timing import (
    accrued_minutes,
    crossing_instant,
    intervals_json,
    project_deadline,
    working_slices,
)
from .intervals import cut_at


# ------------------------------------------------------------------ evidence
def build_basis(bundle, at: datetime, *, include_slices: bool = True,
                slices=None, accrued: Optional[float] = None) -> dict:
    """Evidence snapshot for one warning/escalation decision.

    Contains everything the React UI shows for the "每一次时限裁决的依据":
    accrued working minutes, counted (active) intervals, pause intervals,
    calendar working slices cut at the decision time, and the threshold.
    """
    minutes, active, pauses, open_since, open_pause = accrued_minutes(
        bundle.calendar, bundle.events, at)
    if slices is None:
        slices = working_slices(bundle.calendar, cut_at(active, at))
    return {
        "decided_at": format_ts(at),
        "calendar_version_id": bundle.ticket["calendar_id"],
        "policy_key": bundle.policy.key,
        "policy_version": bundle.policy.version,
        "accrued_work_minutes": round(accrued if accrued is not None
                                      else minutes, 3),
        "counted_intervals": intervals_json(active),
        "pause_intervals": intervals_json(pauses),
        "working_slices": intervals_json(slices) if include_slices else [],
        "clock_running": open_since is not None,
        "open_since": format_ts(open_since) if open_since else None,
        "open_pause_since": format_ts(open_pause) if open_pause else None,
    }


# --------------------------------------------------------------- timer scan
def adjudicate_ticket(db: Database, ticket_id: int,
                      at: Optional[datetime] = None, tx=None) -> List[dict]:
    """Scan one ticket against its *pinned* policy.

    Idempotent: thresholds whose ``(ticket, policy_version, kind, ref)``
    already have an event are skipped.  Pure derivation means a restart
    reaches the same result.
    """
    at = at or now_utc()
    own = tx is None
    if own:
        # Acquire the writer lock BEFORE reading anything.  Otherwise we
        # could read a pre-resolve snapshot and, after a concurrent resolve
        # wins the lock, insert an event whose commit order contradicts the
        # resolved status ("到期与解决竞争").
        tx = db.write_tx()
    try:
        guard = tx.query_one(
            "SELECT status FROM tickets WHERE id = ?", (ticket_id,))
        if guard is None:
            raise KeyError(f"ticket {ticket_id} not found")
        resolved = guard["status"] == "resolved"
        bundle = load_bundle(tx, ticket_id)
        minutes, active, pauses, _os, _op = accrued_minutes(
            bundle.calendar, bundle.events, at)
        slices = working_slices(bundle.calendar, cut_at(active, at))

        existing = {
            (r["kind"], r["ref"])
            for r in tx.query_all(
                "SELECT kind, ref FROM sla_events WHERE ticket_id = ? "
                "AND policy_version = ?",
                (ticket_id, bundle.policy.version))
        }
        registered: List[dict] = []
        for kind, threshold, ref in (
                bundle.policy.thresholds() if not resolved else []):
            if (kind, ref) in existing:
                continue
            crossed = crossing_instant(bundle.calendar, active, threshold)
            if crossed is None:
                continue
            basis = build_basis(
                bundle, at, slices=slices, accrued=minutes)
            basis.update({
                "kind": kind,
                "ref": ref,
                "threshold_minutes": threshold,
                "crossed_at": format_ts(crossed),
                "trigger": "timer",
            })
            tx.execute(
                "INSERT INTO sla_events(ticket_id, policy_version, kind, "
                "ref, threshold_min, crossed_at, registered_at, basis) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (ticket_id, bundle.policy.version, kind, ref, threshold,
                 format_ts(crossed), format_ts(at),
                 json.dumps(basis, ensure_ascii=False)))
            registered.append({"kind": kind, "ref": ref,
                               "crossed_at": format_ts(crossed)})

        tx.execute(
            "UPDATE tickets SET accrued_minutes = ?, accrued_updated_at = ? "
            "WHERE id = ?",
            (round(minutes, 3), format_ts(at), ticket_id))
        if own:
            tx.__exit__(None, None, None)
        return registered
    except Exception:
        if own:
            tx.__exit__(True, None, None)
        raise


def scan_due(db: Database, at: Optional[datetime] = None) -> List[dict]:
    """Scan every non-resolved ticket; returns all freshly registered evs."""
    at = at or now_utc()
    all_new: List[dict] = []
    # One IMMEDIATE tx for the whole sweep => scan vs user actions serialize.
    with db.write_tx() as tx:
        rows = tx.query_all(
            "SELECT id FROM tickets WHERE status != 'resolved' ORDER BY id")
        for r in rows:
            all_new.extend(
                {"ticket_id": r["id"], **e}
                for e in adjudicate_ticket(db, r["id"], at=at, tx=tx))
    return all_new


def snapshot_accrued(db: Database, ticket_id: int, at: datetime,
                     tx=None) -> None:
    """Recompute and persist accrued minutes within an open writer tx.

    Called from user status actions inside the *same* transaction, so the
    stored snapshot (used as the UI view time) advances atomically with the
    state change — even if no later scan ever runs (e.g. resolve as the
    final action).
    """
    own = tx is None
    if own:
        tx = db.write_tx()
    try:
        bundle = load_bundle(tx, ticket_id)
        minutes, _a, _p, _os, _op = accrued_minutes(
            bundle.calendar, bundle.events, at)
        tx.execute(
            "UPDATE tickets SET accrued_minutes = ?, accrued_updated_at = ? "
            "WHERE id = ?",
            (round(minutes, 3), format_ts(at), ticket_id))
        if own:
            tx.__exit__(None, None, None)
    except Exception:
        if own:
            tx.__exit__(True, None, None)
        raise


def preview_migration(bundle, new_policy: Policy, at: datetime) -> dict:
    """Compute the old/new timing difference without writing anything."""
    minutes, active, pauses, _os, _op = accrued_minutes(
        bundle.calendar, bundle.events, at)

    old_thr = {(k, ref): m for k, m, ref in bundle.policy.thresholds()}
    new_thr = {(k, ref): m for k, m, ref in new_policy.thresholds()}
    refs = sorted(old_thr.keys() | new_thr.keys(),
                  key=lambda t: (new_thr.get(t, old_thr.get(t)), t))
    diffs: List[dict] = []
    for key in refs:
        kind, ref = key
        om = old_thr.get(key)
        nm = new_thr.get(key)
        old_cross = (crossing_instant(bundle.calendar, active, om)
                     if om is not None else None)
        new_cross = (crossing_instant(bundle.calendar, active, nm)
                     if nm is not None else None)
        if om is None:
            change = "added"
            delta = None
        elif nm is None:
            change = "removed"
            delta = None
        elif om == nm:
            change = "unchanged"
            delta = 0.0
        elif nm < om:
            change = "tightened"
            delta = round(nm - om, 3)
        elif nm > om:
            change = "relaxed"
            delta = round(nm - om, 3)
        else:
            change = "unchanged"
            delta = 0.0
        diffs.append({
            "kind": kind, "ref": ref,
            "old_minutes": om, "new_minutes": nm,
            "delta_minutes": delta,
            "old_crossed_at": format_ts(old_cross) if old_cross else None,
            "new_crossed_at": format_ts(new_cross) if new_cross else None,
            "old_already_crossed": old_cross is not None,
            "new_already_crossed": new_cross is not None,
            "change": change,
        })

    return {
        "ticket_id": bundle.ticket["id"],
        "old_policy": {"key": bundle.policy.key,
                       "version": bundle.policy.version,
                       "spec": bundle.policy.spec()},
        "new_policy": {"key": new_policy.key,
                       "version": new_policy.version,
                       "spec": new_policy.spec()},
        "accrued_work_minutes": round(minutes, 3),
        "decided_at": format_ts(at),
        "thresholds": diffs,
    }


def apply_migration(db: Database, ticket_id: int, new_policy_key: str,
                    new_policy_version: int,
                    expected_revision: int, at: Optional[datetime] = None,
                    actor: str = "", reason: str = "") -> dict:
    """Explicitly migrate one ticket to a new policy version.

    The diff (old vs new timing + evidence) is recomputed inside the same
    writer transaction and saved to ``migrations``.  The old pinned timing
    stays visible: prior events keep their policy version and are linked to
    the migration instead of being rewritten.
    """
    at = at or now_utc()
    with db.write_tx() as tx:
        bundle = load_bundle(tx, ticket_id)
        if bundle.ticket["revision"] != expected_revision:
            raise StaleRevision(
                f"expected revision {expected_revision}, "
                f"current {bundle.ticket['revision']}")
        new_policy = get_policy_by_version(
            tx, new_policy_key, new_policy_version)
        if (new_policy.key == bundle.policy.key
                and new_policy.version == bundle.policy.version):
            raise ValueError("ticket already on this policy version")

        diff = preview_migration(bundle, new_policy, at)
        new_policy_id = _policy_row_id(tx, new_policy)
        cur = tx.execute(
            "INSERT INTO migrations(ticket_id, old_policy_id, "
            "old_policy_version, new_policy_id, new_policy_version, "
            "diff_json, applied_at, actor, reason, revision_from, revision_to)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ticket_id, bundle.ticket["policy_id"], bundle.policy.version,
             new_policy_id, new_policy.version,
             json.dumps(diff, ensure_ascii=False), format_ts(at), actor,
             reason, bundle.ticket["revision"],
             bundle.ticket["revision"] + 1))
        migration_id = cur.lastrowid

        # Events of the abandoned version stay (evidence) but are linked.
        tx.execute(
            "UPDATE sla_events SET migration_id = ? WHERE ticket_id = ? "
            "AND policy_version = ? AND migration_id IS NULL",
            (migration_id, ticket_id, bundle.policy.version))

        # Backfill thresholds already due under the new policy version.
        # UNIQUE(policy_version, kind, ref) keeps this idempotent.
        minutes, active, _p, _os, _op = accrued_minutes(
            bundle.calendar, bundle.events, at)
        slices = working_slices(bundle.calendar, cut_at(active, at))
        for td in diff["thresholds"]:
            if td["new_minutes"] is None or not td["new_already_crossed"]:
                continue
            crossed = crossing_instant(
                bundle.calendar, active, td["new_minutes"])
            basis = build_basis(bundle, at, slices=slices, accrued=minutes)
            basis.update({
                "kind": td["kind"], "ref": td["ref"],
                "threshold_minutes": td["new_minutes"],
                "crossed_at": format_ts(crossed),
                "trigger": "migration_backfill",
                "migration_id": migration_id,
            })
            tx.execute(
                "INSERT OR IGNORE INTO sla_events(ticket_id, policy_version,"
                " kind, ref, threshold_min, crossed_at, registered_at, "
                "basis, migration_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (ticket_id, new_policy.version, td["kind"], td["ref"],
                 td["new_minutes"], format_ts(crossed), format_ts(at),
                 json.dumps(basis, ensure_ascii=False), migration_id))

        tx.execute(
            "UPDATE tickets SET policy_id = ?, policy_version = ?, "
            "revision = revision + 1, updated_at = ?, migration_id = ? "
            "WHERE id = ?",
            (new_policy_id, new_policy.version, format_ts(at),
             migration_id, ticket_id))
        _log_op(tx, "migrate", ticket_id, at,
                f"v{bundle.policy.version}->v{new_policy.version}")

        return {"migration_id": migration_id, "diff": diff}
