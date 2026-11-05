"""Dropout bookkeeping for one pump.

The pumps' WiFi modules fall off the network from time to time (the pump
keeps running its stored program; only control is lost). This tracker
turns the coordinator's poll outcomes into glanceable diagnostics: when
the pump was last reachable, how many times it has dropped out, and a
short log of recent outages with durations — enough to tell "the MCP-120
drops every night at 2am" from "they all blip when the AP reboots".

Pure logic, no Home Assistant imports, so it is unit-testable; the
coordinator feeds it poll outcomes and the diagnostic sensors read it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

MAX_EVENTS = 20


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass
class DropoutTracker:
    last_contact: datetime | None = None
    offline_since: datetime | None = None
    count: int = 0
    # Completed outages, oldest first: {"start": iso, "end": iso, "duration_s": int}
    events: list[dict[str, Any]] = field(default_factory=list)

    def record_success(self, now: datetime) -> None:
        if self.offline_since is not None:
            self.events.append(
                {
                    "start": _iso(self.offline_since),
                    "end": _iso(now),
                    "duration_s": int((now - self.offline_since).total_seconds()),
                }
            )
            del self.events[:-MAX_EVENTS]
            self.offline_since = None
        self.last_contact = now

    def record_failure(self, now: datetime) -> None:
        if self.offline_since is None:
            self.offline_since = now
            self.count += 1

    def as_attributes(self) -> dict[str, Any]:
        return {
            "last_contact": _iso(self.last_contact),
            "offline_since": _iso(self.offline_since),
            "recent": list(self.events),
        }

    def seed(
        self,
        count: int,
        events: list[dict[str, Any]],
        last_contact: datetime | None,
        offline_since: datetime | None,
    ) -> None:
        """Restore persisted state after a Home Assistant restart. Only
        applied while the tracker is still blank, so a restore can never
        clobber live observations."""
        if self.count or self.events or self.last_contact or self.offline_since:
            return
        self.count = count
        self.events = list(events)[-MAX_EVENTS:]
        self.last_contact = last_contact
        self.offline_since = offline_since
