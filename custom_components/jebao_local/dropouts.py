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
    _seeded: bool = False

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
        """Merge persisted state in after a Home Assistant restart.

        This cannot assume a blank tracker. The coordinator's first
        refresh runs in async_setup_entry, *before* the platforms are
        forwarded, so the tracker has already recorded one poll outcome
        by the time the Dropouts sensor restores its state. Refusing to
        apply the restore in that case silently reset the count to zero
        on every restart - which destroyed exactly the long-run history
        the sensor exists to collect.

        Runs once; later calls are ignored so a reload can't double-count.
        """
        if self._seeded:
            return
        self._seeded = True

        # Restored episodes are older than anything observed since boot.
        self.events = (list(events) + self.events)[-MAX_EVENTS:]

        if offline_since is not None and self.offline_since is not None:
            # The same outage is still running across the restart: the
            # restored count already includes it, and the real start is
            # the one from before the restart, so the duration spans it.
            self.count = count
            self.offline_since = offline_since
        elif offline_since is not None and self.last_contact is not None:
            # It was down before the restart and answered on the first
            # poll after it - so the outage ended somewhere in between.
            # Close it off rather than losing the episode entirely.
            self.count = count
            self.events.append(
                {
                    "start": _iso(offline_since),
                    "end": _iso(self.last_contact),
                    "duration_s": int((self.last_contact - offline_since).total_seconds()),
                }
            )
            del self.events[:-MAX_EVENTS]
        elif offline_since is not None:
            # It was down before the restart and no poll has resolved yet
            # (restore beat the first refresh): adopt the open episode so
            # the eventual recovery still spans the restart.
            self.count = count
            self.offline_since = offline_since
        else:
            # It was reachable before the restart. Anything counted since
            # boot is a genuinely new episode on top of the old total.
            self.count += count

        # Keep whichever contact is more recent: the restored one when the
        # pump hasn't answered since boot, the live one when it has.
        if last_contact is not None and (self.last_contact is None or last_contact > self.last_contact):
            self.last_contact = last_contact
