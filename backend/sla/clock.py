"""UTC timestamp helpers.

All wall-clock values in the system are timezone-aware UTC datetimes on the
inside and ISO-8601 strings (``...+00:00`` / ``Z``) on the wire.
"""
from __future__ import annotations

from datetime import datetime, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: str | datetime) -> datetime:
    """Parse an ISO-8601 string into an aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    else:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_ts(dt: datetime) -> str:
    dt = parse_ts(dt)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
