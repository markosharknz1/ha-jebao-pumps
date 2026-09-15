"""Timed "off" for Jebao devices, mirroring the lights' `lights_off`.

Turn a pump off now and have it come back on its own after a set time -
the feeding/maintenance case. The equivalent already existed only inside
the Lovelace card, as a `setInterval` in the browser: close the tab or
restart Home Assistant and nothing ever turned the pump back on. Leaving
a reef tank with no flow indefinitely is a genuinely bad failure mode, so
the deadline lives in `.storage` here instead and is re-armed on startup.

What's restored is deliberately just the power attribute. The device
keeps its own mode, speed and schedule while it is off, so switching
power back on brings all of that back with it - there is no need to
snapshot and replay settings, and not doing so means a timer can never
clobber a change the user made in the meantime.

A device that was already off when the timer was set is left alone at the
deadline rather than being switched on, since turning something on that
the user had deliberately off would be worse than doing nothing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .fan import SWITCH_NAMES, find_attr_name

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1
_STORE_KEY = f"{DOMAIN}_off_timers"
DATA_OFF_TIMERS = f"{DOMAIN}_off_timers"

# How hard to try to bring a device back. A pump that fails to restart is
# the failure this whole module exists to avoid, so a deadline that lands
# while the device is briefly unreachable retries rather than giving up.
_RESTORE_ATTEMPTS = 6
_RESTORE_RETRY = timedelta(seconds=30)


@dataclass
class PendingOff:
    """A device that is off until `deadline`, and what to do then."""

    did: str
    deadline: datetime
    restore_on: bool  # False when it was already off - leave it off

    def as_dict(self) -> dict:
        return {
            "did": self.did,
            "deadline": self.deadline.isoformat(),
            "restore_on": self.restore_on,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PendingOff | None":
        deadline = dt_util.parse_datetime(data.get("deadline") or "")
        if not data.get("did") or deadline is None:
            return None
        return cls(did=data["did"], deadline=deadline, restore_on=bool(data.get("restore_on", True)))


def power_attr_of(coordinator) -> str | None:
    """The attribute that turns this product on and off, or None.

    Uses the same name list the fan/switch platforms already agree on, so
    a timer can never act on a different attribute than the UI's power
    control does.
    """
    return find_attr_name(coordinator.schema, SWITCH_NAMES, "bool")


class OffTimerManager:
    """Owns every pending timed-off, and their persistence."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, _STORE_VERSION, _STORE_KEY)
        self._pending: dict[str, PendingOff] = {}
        self._unsubs: dict[str, CALLBACK_TYPE] = {}

    # -- persistence ------------------------------------------------------

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        for raw in data.get("timers", []):
            pending = PendingOff.from_dict(raw)
            if pending is not None:
                self._pending[pending.did] = pending

    async def _async_save(self) -> None:
        await self._store.async_save(
            {"timers": [p.as_dict() for p in self._pending.values()]}
        )

    # -- queries ----------------------------------------------------------

    def pending_for(self, did: str) -> PendingOff | None:
        return self._pending.get(did)

    # -- arming -----------------------------------------------------------

    async def async_start(self, coordinator, deadline: datetime) -> None:
        """Switch the device off now and arrange for it to come back."""
        attr = power_attr_of(coordinator)
        if attr is None:
            raise ValueError(f"{coordinator.schema.name_en} has no power control to time")

        data = coordinator.data or {}
        was_on = bool(data.get(attr, True))

        await coordinator.async_write({attr: False})

        self._cancel_callback(coordinator.did)
        self._pending[coordinator.did] = PendingOff(
            did=coordinator.did, deadline=deadline, restore_on=was_on
        )
        await self._async_save()
        self._schedule(coordinator.did, deadline)
        _LOGGER.info(
            "%s off until %s (%s)",
            coordinator.did,
            deadline.isoformat(timespec="seconds"),
            "will switch back on" if was_on else "was already off, will stay off",
        )

    async def async_cancel(self, coordinator, restore: bool = True) -> bool:
        """Drop a pending timer. Returns True if there was one."""
        pending = self._pending.pop(coordinator.did, None)
        self._cancel_callback(coordinator.did)
        if pending is None:
            return False
        await self._async_save()
        if restore and pending.restore_on:
            await self._async_restore(coordinator, attempt=1)
        return True

    async def async_resume_all(self, coordinators: dict[str, object]) -> None:
        """After a restart: fire anything already due, re-arm the rest.

        Timers whose device has since been removed are dropped rather than
        left in storage forever.
        """
        now = dt_util.utcnow()
        for did, pending in list(self._pending.items()):
            coordinator = coordinators.get(did)
            if coordinator is None:
                _LOGGER.debug("Dropping timed-off for unknown device %s", did)
                self._pending.pop(did, None)
                continue
            if pending.deadline <= now:
                _LOGGER.info("Timed-off for %s expired while HA was down - restoring now", did)
                await self._fire(did, coordinator)
            else:
                self._schedule(did, pending.deadline)
        await self._async_save()

    # -- internals --------------------------------------------------------

    def _schedule(self, did: str, when: datetime) -> None:
        async def _due(_now) -> None:
            self._unsubs.pop(did, None)
            coordinator = self._coordinators().get(did)
            if coordinator is None:
                self._pending.pop(did, None)
                await self._async_save()
                return
            await self._fire(did, coordinator)

        self._unsubs[did] = async_track_point_in_time(self.hass, _due, when)

    async def _fire(self, did: str, coordinator) -> None:
        pending = self._pending.pop(did, None)
        await self._async_save()
        if pending is not None and pending.restore_on:
            await self._async_restore(coordinator, attempt=1)

    async def _async_restore(self, coordinator, attempt: int) -> None:
        attr = power_attr_of(coordinator)
        if attr is None:
            return
        try:
            await coordinator.async_write({attr: True})
        except Exception as err:  # noqa: BLE001 - must not give up quietly
            if attempt >= _RESTORE_ATTEMPTS:
                _LOGGER.error(
                    "Could not switch %s back on after its timer (%s attempts): %s - "
                    "turn it on manually",
                    coordinator.did, attempt, err,
                )
                return
            _LOGGER.warning(
                "Failed to switch %s back on (attempt %s/%s): %s - retrying in %ss",
                coordinator.did, attempt, _RESTORE_ATTEMPTS, err,
                int(_RESTORE_RETRY.total_seconds()),
            )

            async def _retry(_now) -> None:
                await self._async_restore(coordinator, attempt + 1)

            async_track_point_in_time(self.hass, _retry, dt_util.utcnow() + _RESTORE_RETRY)
            return
        _LOGGER.info("%s switched back on after its timer", coordinator.did)

    def _coordinators(self) -> dict[str, object]:
        return {c.did: c for c in self.hass.data.get(DOMAIN, {}).values()
                if hasattr(c, "did")}

    def _cancel_callback(self, did: str) -> None:
        unsub = self._unsubs.pop(did, None)
        if unsub is not None:
            unsub()

    def shutdown(self) -> None:
        for did in list(self._unsubs):
            self._cancel_callback(did)


def resolve_deadline(minutes: int | None, until: str | None) -> datetime:
    """Turn the service's `minutes` / `until` into an absolute time.

    `until` is a local wall-clock HH:MM and rolls to tomorrow when it has
    already passed today, so "until 07:00" at 11pm means the morning.
    """
    if (minutes is None) == (until is None):
        raise ValueError("give exactly one of 'minutes' or 'until'")

    if minutes is not None:
        return dt_util.utcnow() + timedelta(minutes=minutes)

    parsed = dt_util.parse_time(until)
    if parsed is None:
        raise ValueError(f"'{until}' is not a time like 07:00")
    now_local = dt_util.now()
    target = now_local.replace(
        hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0
    )
    if target <= now_local:
        target += timedelta(days=1)
    return dt_util.as_utc(target)
