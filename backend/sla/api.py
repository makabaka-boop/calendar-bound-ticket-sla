"""FastAPI application: ticket SLA timing service."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .adjudicate import (
    adjudicate_ticket,
    apply_migration,
    build_basis,
    preview_migration,
    scan_due,
)
from .clock import format_ts, now_utc, parse_ts
from .db import Database
from .policy import Policy
from .seed import DEFAULT_CALENDAR, seed
from .store import (
    IllegalTransition,
    StaleRevision,
    apply_status,
    create_policy_version,
    create_ticket,
    latest_policy,
    list_policies,
    load_bundle,
)
from .timing import accrued_minutes, project_deadline, working_slices
from .intervals import cut_at

DB_PATH = os.environ.get("SLA_DB", "/tmp/sla.db")
SCAN_INTERVAL = float(os.environ.get("SLA_SCAN_INTERVAL", "5"))


# ------------------------------------------------------------------ schemas
class PolicySpecIn(BaseModel):
    policy_key: str = "standard"
    name: str = "Standard SLA"
    description: str = ""
    warn_minutes: float
    escalations: List[Dict[str, Any]] = Field(default_factory=list)


class TicketIn(BaseModel):
    title: str
    policy_key: str = "standard"
    policy_version: Optional[int] = None
    calendar_spec: Optional[Dict[str, Any]] = None
    at: Optional[str] = None        # test hook: override event time
    note: str = ""


class StatusIn(BaseModel):
    action: str                     # pause|resume|resolve|reopen
    revision: int
    at: Optional[str] = None
    note: str = ""
    actor: str = ""


class ScanIn(BaseModel):
    at: Optional[str] = None
    ticket_id: Optional[int] = None


class PolicyVersionIn(BaseModel):
    policy_key: str = "standard"
    name: str = "Standard SLA"
    description: str = ""
    spec: Dict[str, Any]


class MigratePreviewIn(BaseModel):
    policy_key: str
    policy_version: int
    at: Optional[str] = None


class MigrateApplyIn(BaseModel):
    policy_key: str
    policy_version: int
    revision: int
    at: Optional[str] = None
    actor: str = ""
    reason: str = ""


# ------------------------------------------------------------- presentation
def _at(value: Optional[str]):
    return parse_ts(value) if value else now_utc()


def serialize_ticket(db: Database, ticket_id: int,
                     at=None) -> Dict[str, Any]:
    """Serialize a ticket as of time ``at``.

    When ``at`` is omitted the view time is the last adjudication tick
    (``accrued_updated_at``) when present, otherwise the real current time.
    This keeps a paused/old ticket's displayed counted intervals stable
    across later reads instead of accruing wall time that no scan has
    adjudicated.
    """
    real_now = now_utc()
    with db.read_tx() as tx:
        row = tx.query_one("SELECT * FROM tickets WHERE id = ?",
                           (ticket_id,))
        if row is None:
            raise KeyError(f"ticket {ticket_id} not found")
        view_at = at or (parse_ts(row["accrued_updated_at"])
                         if row["accrued_updated_at"] else real_now)
        bundle = load_bundle(tx, ticket_id)
        minutes, active, pauses, open_since, open_pause = accrued_minutes(
            bundle.calendar, bundle.events, view_at)
        slices = working_slices(bundle.calendar, cut_at(active, view_at))
        events = [
            {"seq": e.seq, "kind": e.kind, "at": format_ts(e.at),
             "note": e.note}
            for e in bundle.events
        ]
        sla_rows = tx.query_all(
            "SELECT * FROM sla_events WHERE ticket_id = ? ORDER BY "
            "crossed_at, id", (ticket_id,))
        migrations = [
            dict(r) for r in tx.query_all(
                "SELECT * FROM migrations WHERE ticket_id = ? ORDER BY id",
                (ticket_id,))
        ]
        t = bundle.ticket
    at = view_at

    # Projected deadline per threshold under current state.
    deadlines = []
    for kind, threshold, ref in bundle.policy.thresholds():
        when = project_deadline(bundle.calendar, bundle.events,
                                threshold, at)
        deadlines.append({
            "kind": kind, "ref": ref, "threshold_minutes": threshold,
            "deadline_at": format_ts(when) if when else None,
            "suspended": when is None,
        })

    if open_pause is not None:
        pauses = list(pauses) + [(open_pause, at)]

    sla_events = []
    for r in sla_rows:
        basis = json.loads(r["basis"])
        sla_events.append({
            "id": r["id"],
            "policy_version": r["policy_version"],
            "kind": r["kind"],
            "ref": r["ref"],
            "threshold_minutes": r["threshold_min"],
            "crossed_at": r["crossed_at"],
            "registered_at": r["registered_at"],
            "migration_id": r["migration_id"],
            "basis": basis,
        })

    for m in migrations:
        m["diff"] = json.loads(m.pop("diff_json"))

    return {
        "id": t["id"],
        "title": t["title"],
        "status": t["status"],
        "revision": t["revision"],
        "created_at": t["created_at"],
        "updated_at": t["updated_at"],
        "policy": {"key": bundle.policy.key,
                   "version": bundle.policy.version,
                   "name": bundle.policy.name,
                   "spec": bundle.policy.spec()},
        "calendar_version_id": t["calendar_id"],
        "calendar_spec": bundle.calendar.to_spec(),
        "accrued_work_minutes": round(minutes, 3),
        "stored_accrued_minutes": t["accrued_minutes"],
        "clock_running": open_since is not None,
        "open_since": format_ts(open_since) if open_since else None,
        "open_pause_since": format_ts(open_pause) if open_pause else None,
        "deadlines": deadlines,
        "counted_intervals": [[format_ts(s), format_ts(e)]
                              for s, e in active],
        "pause_intervals": [[format_ts(s), format_ts(e)]
                            for s, e in pauses],
        "working_slices": [[format_ts(s), format_ts(e)]
                           for s, e in slices],
        "state_events": events,
        "sla_events": sla_events,
        "migrations": migrations,
        "now": format_ts(at),
    }


# ------------------------------------------------------------------- factory
def create_app(db_path: str = DB_PATH, start_scanner: bool = True,
               reseed: bool = True) -> FastAPI:
    db = Database(db_path)
    if reseed:
        try:
            seed(db)
        except Exception:
            pass

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()

        def worker():
            while not stop.wait(SCAN_INTERVAL):
                try:
                    scan_due(db)
                except Exception as exc:  # never let the loop die
                    print(f"[scanner] {exc!r}")

        thread = threading.Thread(target=worker, name="sla-scanner",
                                  daemon=True)
        if start_scanner:
            thread.start()
        app.state.stop_event = stop
        app.state.scanner = thread
        yield
        stop.set()
        if start_scanner:
            thread.join(timeout=2)

    app = FastAPI(title="SLA Ticket Timer", lifespan=lifespan)
    app.state.db = db

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    # ------------------------------------------------------------ policies
    @app.get("/api/policies")
    def get_policies(policy_key: Optional[str] = None):
        with db.read_tx() as tx:
            items = list_policies(tx, policy_key)
            out = []
            for p in items:
                latest = latest_policy(tx, p.key).version
                out.append({
                    "policy_key": p.key, "version": p.version,
                    "name": p.name, "description": p.description,
                    "spec": p.spec(),
                    "is_latest": p.version == latest,
                })
        return out

    @app.post("/api/policies", status_code=201)
    def post_policy(body: PolicyVersionIn):
        # A new version is immutable; old tickets keep their pinned version.
        spec = body.spec
        if "warn_minutes" not in spec:
            raise HTTPException(422, "spec.warn_minutes is required")
        policy = create_policy_version(
            db, body.policy_key, spec, name=body.name,
            description=body.description)
        return {"policy_key": policy.key, "version": policy.version,
                "spec": policy.spec(), "reused": False}

    # ------------------------------------------------------------- tickets
    @app.post("/api/tickets", status_code=201)
    def post_ticket(body: TicketIn):
        at = _at(body.at)
        try:
            ticket = create_ticket(
                db, title=body.title, policy_key=body.policy_key,
                policy_version=body.policy_version,
                calendar_spec=body.calendar_spec or DEFAULT_CALENDAR,
                at=at, note=body.note)
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        # Immediately adjudicate (e.g. backdated creation).
        adjudicate_ticket(db, ticket["id"], at=at)
        return serialize_ticket(db, ticket["id"], at=at)

    @app.get("/api/tickets")
    def list_tickets(status: Optional[str] = None):
        with db.read_tx() as tx:
            sql = "SELECT * FROM tickets"
            params: tuple = ()
            if status:
                sql += " WHERE status = ?"
                params = (status,)
            sql += " ORDER BY id"
            rows = tx.query_all(sql, params)
            result = []
            now = now_utc()
            for r in rows:
                result.append({
                    "id": r["id"], "title": r["title"],
                    "status": r["status"], "revision": r["revision"],
                    "policy_version": r["policy_version"],
                    "accrued_work_minutes": r["accrued_minutes"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                })
        return result

    @app.get("/api/tickets/{ticket_id}")
    def get_ticket(ticket_id: int):
        try:
            return serialize_ticket(db, ticket_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc))

    @app.post("/api/tickets/{ticket_id}/status")
    def post_status(ticket_id: int, body: StatusIn):
        at = _at(body.at)
        try:
            apply_status(db, ticket_id, body.action, body.revision,
                         at=at, note=body.note, actor=body.actor)
        except StaleRevision as exc:
            raise HTTPException(409, f"修订冲突: {exc}")
        except IllegalTransition as exc:
            raise HTTPException(409, f"非法状态转移: {exc}")
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        adjudicate_ticket(db, ticket_id, at=at)
        return serialize_ticket(db, ticket_id, at=at)

    # --------------------------------------------------------------- timer
    @app.post("/api/admin/scan")
    def admin_scan(body: ScanIn):
        at = _at(body.at)
        if body.ticket_id is not None:
            try:
                new = adjudicate_ticket(db, body.ticket_id, at=at)
            except KeyError as exc:
                raise HTTPException(404, str(exc))
            return {"scanned": [body.ticket_id], "registered": new}
        new = scan_due(db, at=at)
        return {"scanned": "all", "registered": new}

    # ----------------------------------------------------------- migration
    @app.post("/api/tickets/{ticket_id}/migrate/preview")
    def migrate_preview(ticket_id: int, body: MigratePreviewIn):
        at = _at(body.at)
        with db.read_tx() as tx:
            try:
                bundle = load_bundle(tx, ticket_id)
                target = __import__(
                    "sla.store", fromlist=["get_policy_by_version"]
                ).get_policy_by_version(
                    tx, body.policy_key, body.policy_version)
            except KeyError as exc:
                raise HTTPException(404, str(exc))
            return preview_migration(bundle, target, at)

    @app.post("/api/tickets/{ticket_id}/migrate")
    def migrate_apply(ticket_id: int, body: MigrateApplyIn):
        at = _at(body.at)
        try:
            result = apply_migration(
                db, ticket_id, body.policy_key, body.policy_version,
                expected_revision=body.revision, at=at,
                actor=body.actor, reason=body.reason)
        except StaleRevision as exc:
            raise HTTPException(409, f"修订冲突: {exc}")
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        adjudicate_ticket(db, ticket_id, at=at)
        return {"result": result,
                "ticket": serialize_ticket(db, ticket_id, at=at)}

    @app.get("/api/tickets/{ticket_id}/migrations")
    def list_migrations(ticket_id: int):
        with db.read_tx() as tx:
            rows = tx.query_all(
                "SELECT * FROM migrations WHERE ticket_id = ? ORDER BY id",
                (ticket_id,))
            out = []
            for r in rows:
                d = dict(r)
                d["diff"] = json.loads(d.pop("diff_json"))
                out.append(d)
        return out

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


app = create_app()
