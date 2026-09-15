"""Tests for the timed-off feature (off_timer.py).

The point of this module is that a pump can't be left off forever, so the
cases worth testing are the ones where something goes wrong: Home
Assistant restarts across the deadline, the device was already off, or the
write to bring it back fails. Those are covered here against a fake
coordinator and a fake store - the timer plumbing itself (async_track_
point_in_time) is stubbed, since what matters is *when* we ask to be woken
and what we do when we are.
"""
import sys
from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from homeassistant.util import dt as dt_util  # noqa: E402

from custom_components.jebao_local import off_timer as off_timer_mod  # noqa: E402
from custom_components.jebao_local.off_timer import (  # noqa: E402
    OffTimerManager,
    PendingOff,
    power_attr_of,
    resolve_deadline,
)
from custom_components.jebao_local.jebao_gizwits.schema import load_by_product_key  # noqa: E402

WAVEMAKER_PRODUCT_KEY = "54114ccdac1e41c0bb17e222887c07ba"


# --- resolve_deadline ------------------------------------------------------


def test_minutes_gives_a_deadline_that_far_ahead():
    before = dt_util.utcnow()
    deadline = resolve_deadline(30, None)
    assert timedelta(minutes=29) < deadline - before < timedelta(minutes=31)


def test_until_rolls_to_tomorrow_when_the_time_has_passed():
    now_local = dt_util.now()
    an_hour_ago = (now_local - timedelta(hours=1)).strftime("%H:%M")
    deadline = resolve_deadline(None, an_hour_ago)
    assert deadline > dt_util.utcnow()
    # ~23h away, not ~-1h.
    assert deadline - dt_util.utcnow() > timedelta(hours=22)


def test_until_later_today_stays_today():
    now_local = dt_util.now()
    in_two_hours = (now_local + timedelta(hours=2)).strftime("%H:%M")
    deadline = resolve_deadline(None, in_two_hours)
    assert timedelta(hours=1) < deadline - dt_util.utcnow() < timedelta(hours=3)


@pytest.mark.parametrize("minutes,until", [(None, None), (30, "07:00")])
def test_exactly_one_duration_is_required(minutes, until):
    with pytest.raises(ValueError):
        resolve_deadline(minutes, until)


def test_a_nonsense_until_is_rejected():
    with pytest.raises(ValueError):
        resolve_deadline(None, "breakfast")


# --- PendingOff ------------------------------------------------------------


def test_pending_off_survives_a_storage_round_trip():
    original = PendingOff(did="aabbcc", deadline=dt_util.utcnow(), restore_on=False)
    restored = PendingOff.from_dict(original.as_dict())
    assert restored == original


@pytest.mark.parametrize("raw", [{}, {"did": "x"}, {"did": "x", "deadline": "soon"}, {"deadline": None}])
def test_unreadable_stored_timers_are_dropped_not_crashed_on(raw):
    assert PendingOff.from_dict(raw) is None


# --- manager ---------------------------------------------------------------


class _FakeCoordinator:
    def __init__(self, did="aabbcc", on=True, fail_writes=False):
        self.did = did
        self.schema = load_by_product_key(WAVEMAKER_PRODUCT_KEY)
        self.data = {power_attr_of(self): on}
        self.writes = []
        self.fail_writes = fail_writes

    async def async_write(self, values):
        if self.fail_writes:
            raise OSError("pump unreachable")
        self.writes.append(values)
        self.data.update(values)


class _FakeStore:
    def __init__(self, *args, **kwargs):
        self.saved = None

    async def async_load(self):
        return self.saved

    async def async_save(self, data):
        self.saved = data


class _FakeHass:
    def __init__(self):
        self.data = {}


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setattr(off_timer_mod, "Store", _FakeStore)
    scheduled = []

    def _fake_track(hass, action, when):
        scheduled.append((when, action))
        return lambda: scheduled.remove((when, action))

    monkeypatch.setattr(off_timer_mod, "async_track_point_in_time", _fake_track)
    mgr = OffTimerManager(_FakeHass())
    mgr.scheduled = scheduled  # test-only handle on what we asked to be woken for
    return mgr


async def _fire_due(manager, coordinator):
    """Run the callback the manager armed, as HA would at the deadline."""
    manager.hass.data.setdefault("jebao_local", {})["e1"] = coordinator
    _when, action = manager.scheduled.pop()
    await action(dt_util.utcnow())


@pytest.mark.asyncio
async def test_starting_a_timer_turns_the_pump_off_and_arms_a_wakeup(manager):
    coordinator = _FakeCoordinator()
    attr = power_attr_of(coordinator)
    deadline = dt_util.utcnow() + timedelta(minutes=15)

    await manager.async_start(coordinator, deadline)

    assert coordinator.writes == [{attr: False}]
    assert manager.pending_for(coordinator.did).restore_on is True
    assert manager.scheduled[0][0] == deadline


@pytest.mark.asyncio
async def test_the_deadline_switches_the_pump_back_on(manager):
    coordinator = _FakeCoordinator()
    attr = power_attr_of(coordinator)
    await manager.async_start(coordinator, dt_util.utcnow() + timedelta(minutes=15))

    await _fire_due(manager, coordinator)

    assert coordinator.writes == [{attr: False}, {attr: True}]
    assert manager.pending_for(coordinator.did) is None


@pytest.mark.asyncio
async def test_a_pump_that_was_already_off_is_left_off(manager):
    coordinator = _FakeCoordinator(on=False)
    attr = power_attr_of(coordinator)
    await manager.async_start(coordinator, dt_util.utcnow() + timedelta(minutes=15))
    assert manager.pending_for(coordinator.did).restore_on is False

    await _fire_due(manager, coordinator)

    assert coordinator.writes == [{attr: False}]  # never switched on


@pytest.mark.asyncio
async def test_cancelling_brings_the_pump_straight_back(manager):
    coordinator = _FakeCoordinator()
    attr = power_attr_of(coordinator)
    await manager.async_start(coordinator, dt_util.utcnow() + timedelta(minutes=15))

    assert await manager.async_cancel(coordinator) is True

    assert coordinator.writes == [{attr: False}, {attr: True}]
    assert manager.pending_for(coordinator.did) is None
    assert manager.scheduled == []  # the wakeup was dropped too


@pytest.mark.asyncio
async def test_cancelling_with_nothing_pending_does_nothing(manager):
    coordinator = _FakeCoordinator()
    assert await manager.async_cancel(coordinator) is False
    assert coordinator.writes == []


@pytest.mark.asyncio
async def test_a_failed_restore_retries_rather_than_giving_up(manager):
    coordinator = _FakeCoordinator(fail_writes=True)
    manager._pending[coordinator.did] = PendingOff(
        did=coordinator.did, deadline=dt_util.utcnow(), restore_on=True
    )

    await manager._fire(coordinator.did, coordinator)

    assert len(manager.scheduled) == 1  # a retry is armed


# --- restart resume --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_timer_that_expired_while_ha_was_down_restores_immediately(manager):
    coordinator = _FakeCoordinator()
    attr = power_attr_of(coordinator)
    manager._pending[coordinator.did] = PendingOff(
        did=coordinator.did,
        deadline=dt_util.utcnow() - timedelta(minutes=5),
        restore_on=True,
    )

    await manager.async_resume_all({coordinator.did: coordinator})

    assert coordinator.writes == [{attr: True}]
    assert manager.pending_for(coordinator.did) is None


@pytest.mark.asyncio
async def test_a_timer_still_in_the_future_is_re_armed(manager):
    coordinator = _FakeCoordinator()
    deadline = dt_util.utcnow() + timedelta(minutes=5)
    manager._pending[coordinator.did] = PendingOff(
        did=coordinator.did, deadline=deadline, restore_on=True
    )

    await manager.async_resume_all({coordinator.did: coordinator})

    assert coordinator.writes == []  # not yet
    assert manager.scheduled[0][0] == deadline


@pytest.mark.asyncio
async def test_a_timer_for_a_device_that_is_gone_is_forgotten(manager):
    manager._pending["ghost"] = PendingOff(
        did="ghost", deadline=dt_util.utcnow() + timedelta(minutes=5), restore_on=True
    )

    await manager.async_resume_all({})

    assert manager.pending_for("ghost") is None
    assert manager.scheduled == []


@pytest.mark.asyncio
async def test_pending_timers_are_reloaded_from_storage(manager):
    deadline = dt_util.utcnow() + timedelta(minutes=5)
    manager._store.saved = {
        "timers": [
            {"did": "aabbcc", "deadline": deadline.isoformat(), "restore_on": True},
            {"did": "broken", "deadline": "not-a-time", "restore_on": True},
        ]
    }

    await manager.async_load()

    assert manager.pending_for("aabbcc").deadline == deadline
    assert manager.pending_for("broken") is None
