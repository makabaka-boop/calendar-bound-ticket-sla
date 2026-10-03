"""SLA policy.

A policy states, in *effective working minutes*:

* ``warn_minutes``: accumulated work minutes at which the ticket gets a
  warning;
* ``escalations``: an ordered list of ``(level, minutes, action)`` thresholds
  at which the ticket is escalated.

Policies are versioned and immutable.  Creating a ticket pins the exact
policy version; later policy edits create a new version and never affect
existing tickets unless an explicit migration is performed (with an
evidence record of the old/new timing difference).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class EscalationRule:
    level: str
    minutes: float
    action: str = ""

    def to_dict(self) -> dict:
        return {"level": self.level, "minutes": self.minutes, "action": self.action}


@dataclass
class Policy:
    key: str
    version: int
    name: str
    warn_minutes: float
    deadlines: List[EscalationRule] = field(default_factory=list)
    description: str = ""

    @classmethod
    def from_row(cls, key: str, version: int, name: str, spec: str | dict,
                 description: str = "") -> "Policy":
        raw = json.loads(spec) if isinstance(spec, str) else spec
        warn = float(raw["warn_minutes"])
        rules = [
            EscalationRule(
                level=str(e["level"]),
                minutes=float(e["minutes"]),
                action=str(e.get("action", "")),
            )
            for e in raw.get("escalations", [])
        ]
        rules.sort(key=lambda r: r.minutes)
        return cls(key=key, version=version, name=name,
                   warn_minutes=warn, deadlines=rules,
                   description=description)

    def spec(self) -> dict:
        return {
            "warn_minutes": self.warn_minutes,
            "escalations": [e.to_dict() for e in self.deadlines],
        }

    def to_json(self) -> str:
        return json.dumps(self.spec(), ensure_ascii=False, sort_keys=True)

    # ------------------------------------------------------------- helpers
    @property
    def max_minutes(self) -> float:
        if self.deadlines:
            return max(self.warn_minutes, self.deadlines[-1].minutes)
        return self.warn_minutes

    def thresholds(self) -> List[tuple[str, float, str]]:
        """All thresholds in chronological order as (kind, minutes, ref)."""
        out: List[tuple[str, float, str]] = [("warn", self.warn_minutes, "warning")]
        out.extend(("escalate", e.minutes, e.level) for e in self.deadlines)
        out.sort(key=lambda t: t[1])
        return out

    def is_same_timing(self, other: "Policy") -> bool:
        return (self.warn_minutes == other.warn_minutes
                and [(e.level, e.minutes) for e in self.deadlines]
                    == [(e.level, e.minutes) for e in other.deadlines])
