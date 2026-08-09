"""Connection handling in the coordinator.

Written from a real failure on a live install: repeated
`ConnectionResetError: [Errno 104] Connection reset by peer` during
authenticate(), surfacing as HA's "Unexpected error fetching ... data"
with a full traceback (SPEC.md Phase 24). Two separate bugs were behind
that, both covered here:

1. A session whose connect() succeeded but authenticate() failed was
   never closed, and one whose read failed was discarded without
   closing. Every failed poll leaked a TCP connection to a device that
   tolerates very few.
2. The post-rediscovery read sat outside any exception handler, so a
   failure there escaped raw instead of becoming UpdateFailed.

Sessions are no longer held between polls at all (SPEC.md Phase 25):
the protocol wants a ~4s heartbeat that this client never sends, so an
idle session was being dropped by the device anyway. Every operation
now opens and closes its own connection, which is what these tests
assert - no socket may survive an operation, successful or not.

Needs the real `homeassistant` package; skips cleanly without it.
"""
import asyncio
import sys
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: E402

from custom_components.jebao_local.coordinator import JebaoLocalCoordinator  # noqa: E402
from custom_components.jebao_local.jebao_gizwits.session import ProtocolError  # noqa: E402


class FakeSession:
    """Records whether it was closed, so leaks are detectable."""

    instances: list["FakeSession"] = []

    def __init__(self, ip, port=None):
        self.ip = ip
        self.closed = False
        self.connected = False
        self.authenticated = False
        FakeSession.instances.append(self)

    # behaviour knobs, set by the tests via FakeSession.behaviour
    behaviour: dict = {}

    async def connect(self):
        if self.behaviour.get("connect_fails"):
            raise ConnectionResetError(104, "Connection reset by peer")
        self.connected = True

    async def authenticate(self):
        if self.behaviour.get("auth_fails"):
            raise ConnectionResetError(104, "Connection reset by peer")
        self.authenticated = True

    async def read_status(self):
        if self.behaviour.get("read_hangs"):
            await asyncio.sleep(3600)
        if self.behaviour.get("read_fails"):
            raise ConnectionResetError(104, "Connection reset by peer")
        return b"\x00" * 512

    async def send_control(self, payload):
        if self.behaviour.get("write_fails_times", 0) > 0:
            self.behaviour["write_fails_times"] -= 1
            raise ConnectionResetError(104, "Connection reset by peer")

    async def close(self):
        self.closed = True


def make_coordinator(monkeypatch, **behaviour):
    """A coordinator with its HA plumbing and session class stubbed out."""
    import custom_components.jebao_local.coordinator as mod

    FakeSession.instances = []
    FakeSession.behaviour = behaviour
    monkeypatch.setattr(mod, "GizwitsSession", FakeSession)

    coordinator = JebaoLocalCoordinator.__new__(JebaoLocalCoordinator)
    coordinator.host = "10.0.0.5"
    coordinator.did = "testdid"
    coordinator.schema = _FakeSchema()
    coordinator.logger = mod._LOGGER
    return coordinator


class _FakeSchema:
    def decode_status(self, raw):
        return {"ok": True}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --- the socket leak -----------------------------------------------------


def test_failed_authenticate_closes_the_socket(monkeypatch):
    c = make_coordinator(monkeypatch, auth_fails=True)

    async def use():
        async with c._session_scope():
            pass

    with pytest.raises(ConnectionResetError):
        run(use())
    assert len(FakeSession.instances) == 1
    assert FakeSession.instances[0].connected, "connect() ran, so there is a socket to leak"
    assert FakeSession.instances[0].closed, "socket leaked - this is what exhausts the pump"


def test_a_successful_operation_also_closes_its_socket(monkeypatch):
    """Connections are per-operation now; none may be left open."""
    c = make_coordinator(monkeypatch)
    assert run(c._async_update_data()) == {"ok": True}
    assert FakeSession.instances
    assert all(s.closed for s in FakeSession.instances)


def test_each_poll_uses_a_fresh_connection(monkeypatch):
    """No session is reused across polls - an idle one gets dropped by the
    device, since we never send the protocol's ~4s heartbeat."""
    c = make_coordinator(monkeypatch)
    run(c._async_update_data())
    run(c._async_update_data())
    assert len(FakeSession.instances) == 2
    assert all(s.closed for s in FakeSession.instances)


def test_repeated_failures_do_not_accumulate_open_sockets(monkeypatch):
    c = make_coordinator(monkeypatch, auth_fails=True)
    # Stub rediscovery, or each poll spends the real UDP discovery window.
    monkeypatch.setattr(c, "_try_recover_via_discovery", _async_return(False))
    for _ in range(5):
        with pytest.raises(UpdateFailed):
            run(c._async_update_data())
    assert FakeSession.instances, "expected sessions to have been attempted"
    assert all(s.closed for s in FakeSession.instances), (
        f"{sum(not s.closed for s in FakeSession.instances)} sockets left open"
    )


# --- failures become UpdateFailed, not a raw traceback -------------------


def test_unreachable_pump_raises_updatefailed_not_connectionreset(monkeypatch):
    c = make_coordinator(monkeypatch, auth_fails=True)
    monkeypatch.setattr(c, "_try_recover_via_discovery", _async_return(True))
    with pytest.raises(UpdateFailed):
        run(c._async_update_data())


def test_failure_after_successful_rediscovery_is_still_updatefailed(monkeypatch):
    """The exact shape of the reported bug: rediscovery says the pump is
    there, the retry then fails anyway, and that read used to sit outside
    any handler."""
    c = make_coordinator(monkeypatch, read_fails=True)
    monkeypatch.setattr(c, "_try_recover_via_discovery", _async_return(True))
    with pytest.raises(UpdateFailed):
        run(c._async_update_data())


def test_rediscovery_failing_is_also_updatefailed(monkeypatch):
    c = make_coordinator(monkeypatch, read_fails=True)

    async def boom():
        raise OSError("network down")

    monkeypatch.setattr(c, "_try_recover_via_discovery", boom)
    with pytest.raises(UpdateFailed):
        run(c._async_update_data())


def test_a_recovered_pump_still_returns_data(monkeypatch):
    """A transient failure must not be turned into a hard error: the
    second attempt, with a fresh session, should succeed."""
    c = make_coordinator(monkeypatch, read_fails=True)

    async def stop_failing():
        FakeSession.behaviour["read_fails"] = False
        return True

    monkeypatch.setattr(c, "_try_recover_via_discovery", stop_failing)
    assert run(c._async_update_data()) == {"ok": True}


# --- the hang ------------------------------------------------------------


def test_a_hung_read_times_out_instead_of_stalling_forever(monkeypatch):
    """HA puts no timeout around a coordinator update, so without our own
    one a half-open socket stops this pump updating until a restart."""
    import custom_components.jebao_local.coordinator as mod

    monkeypatch.setattr(mod, "SESSION_TIMEOUT", 0.05)
    c = make_coordinator(monkeypatch, read_hangs=True)
    monkeypatch.setattr(c, "_try_recover_via_discovery", _async_return(False))
    with pytest.raises(UpdateFailed):
        run(c._async_update_data())


# --- writes --------------------------------------------------------------


def test_write_retries_once_with_a_fresh_session(monkeypatch):
    c = make_coordinator(monkeypatch, write_fails_times=1)
    sent = []

    async def fake_refresh():
        sent.append("refreshed")

    monkeypatch.setattr(c, "async_request_refresh", fake_refresh)
    import custom_components.jebao_local.jebao_gizwits.control as control

    monkeypatch.setattr(control, "build_control_payload", lambda schema, changes: b"\x01")
    run(c.async_write({"SwitchON": True}))
    assert sent == ["refreshed"], "write should have succeeded on the retry"
    assert FakeSession.instances[0].closed, "the failed session must be closed, not reused"


def test_write_that_keeps_failing_propagates(monkeypatch):
    c = make_coordinator(monkeypatch, write_fails_times=99)
    import custom_components.jebao_local.jebao_gizwits.control as control

    monkeypatch.setattr(control, "build_control_payload", lambda schema, changes: b"\x01")
    with pytest.raises(ConnectionResetError):
        run(c.async_write({"SwitchON": True}))
    assert all(s.closed for s in FakeSession.instances)


def _async_return(value):
    async def _inner():
        return value

    return _inner
