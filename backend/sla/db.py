"""SQLite persistence and serialized write transactions.

Concurrency model
-----------------
Every state-changing operation — background timer adjudication *and* user
status actions — goes through :func:`write_tx`, which opens
``BEGIN IMMEDIATE``.  SQLite hands out one writer lock at a time, so all
operations commit in one global serial order.  Tickets additionally carry a
monotonic ``revision``: state actions must present the revision they read,
and a stale submit gets HTTP 409 ("过期修订提交返回冲突").

Timer idempotency
-----------------
``sla_events`` has a unique ``(ticket_id, policy_version, kind, ref)`` key
scoped to the *pinned policy version*.  Repeated scans / restarts find the
same crossing instants (they are pure functions of the append-only event
log) and cannot register a warning or escalation twice.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Iterable, Optional

from .clock import format_ts, parse_ts

SCHEMA = """
CREATE TABLE IF NOT EXISTS calendars (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    spec        TEXT NOT NULL,
    spec_hash   TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_key  TEXT NOT NULL,
    version     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    spec        TEXT NOT NULL,
    spec_hash   TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE (policy_key, version),
    UNIQUE (policy_key, spec_hash)
);

CREATE TABLE IF NOT EXISTS tickets (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'open',
    policy_id        INTEGER NOT NULL,
    policy_version   INTEGER NOT NULL,
    calendar_id      INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    revision         INTEGER NOT NULL DEFAULT 1,
    accrued_minutes  REAL NOT NULL DEFAULT 0,
    accrued_updated_at TEXT,
    migration_id     INTEGER,
    FOREIGN KEY (policy_id) REFERENCES policies(id),
    FOREIGN KEY (calendar_id) REFERENCES calendars(id)
);

CREATE TABLE IF NOT EXISTS ticket_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL,
    seq         INTEGER NOT NULL,
    kind        TEXT NOT NULL,          -- open|pause|resume|reopen|resolve
    at          TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    UNIQUE (ticket_id, seq),
    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
);
CREATE INDEX IF NOT EXISTS idx_ticket_events_ticket
    ON ticket_events(ticket_id);

CREATE TABLE IF NOT EXISTS sla_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id       INTEGER NOT NULL,
    policy_version  INTEGER NOT NULL,
    kind            TEXT NOT NULL,      -- warn|escalate
    ref             TEXT NOT NULL,      -- 'warning' or escalation level
    threshold_min   REAL NOT NULL,
    crossed_at      TEXT NOT NULL,
    registered_at   TEXT NOT NULL,
    basis           TEXT NOT NULL,      -- evidence JSON
    migration_id    INTEGER,
    UNIQUE (ticket_id, policy_version, kind, ref),
    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
);
CREATE INDEX IF NOT EXISTS idx_sla_ticket ON sla_events(ticket_id);

CREATE TABLE IF NOT EXISTS migrations (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id            INTEGER NOT NULL,
    old_policy_id        INTEGER NOT NULL,
    old_policy_version   INTEGER NOT NULL,
    new_policy_id        INTEGER NOT NULL,
    new_policy_version   INTEGER NOT NULL,
    diff_json            TEXT NOT NULL, -- old/new timing comparison
    applied_at           TEXT NOT NULL,
    actor                TEXT NOT NULL DEFAULT '',
    reason               TEXT NOT NULL DEFAULT '',
    revision_from        INTEGER NOT NULL,
    revision_to          INTEGER NOT NULL,
    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
);

-- Global serial order of every writer transaction (timer + user actions).
CREATE TABLE IF NOT EXISTS ops_log (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    op          TEXT NOT NULL,
    ticket_id   INTEGER,
    at          TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT ''
);
"""

WAL_PRAGMAS = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
]


class Database:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        # check_same_thread=False: FastAPI handlers + scanner may touch the
        # DB from different threads; every connection opens its own tx.
        self._check = not path.startswith("file:") or True
        self._init()

    def _connect(self, readonly: bool = False) -> sqlite3.Connection:
        if readonly and self.path != ":memory:":
            conn = sqlite3.connect(
                f"file:{self.path}?mode=ro", check_same_thread=False,
                isolation_level=None, uri=True)
        else:
            conn = sqlite3.connect(self.path, check_same_thread=False,
                                   isolation_level=None)
        conn.row_factory = sqlite3.Row
        for p in WAL_PRAGMAS:
            conn.execute(p)
        return conn

    def _init(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    # ------------------------------------------------------------- sessions
    def read_tx(self):
        # Read-only connection: it can never take the writer lock, so it
        # always sees a committed snapshot and cannot interleave with an
        # in-flight IMMEDIATE transaction.
        return _Transaction(self._connect(readonly=True), immediate=False)

    def write_tx(self):
        return _Transaction(self._connect(), immediate=True)


class _Transaction:
    def __init__(self, conn: sqlite3.Connection, immediate: bool):
        self.conn = conn
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
        else:
            conn.execute("BEGIN")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        self.conn.close()
        return False

    # ------------------------------------------------------------------ CRUD
    def execute(self, sql: str, params: Iterable[Any] = ()):
        cur = self.conn.execute(sql, tuple(params))
        return cur

    def query_one(self, sql: str, params: Iterable[Any] = ()
                  ) -> Optional[sqlite3.Row]:
        return self.conn.execute(sql, tuple(params)).fetchone()

    def query_all(self, sql: str, params: Iterable[Any] = ()
                  ) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(params)).fetchall()


def spec_hash(spec: dict | str) -> str:
    import hashlib
    if not isinstance(spec, str):
        spec = json.dumps(spec, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(spec.encode("utf-8")).hexdigest()[:16]
