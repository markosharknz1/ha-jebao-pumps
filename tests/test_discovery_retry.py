"""discover() used to send exactly one UDP broadcast - a real user with 5
pumps consistently saw only 4, because UDP is lossy and these are cheap
WiFi modules on a congested 2.4GHz network. It now re-probes across the
listen window, so one dropped packet (in either direction) no longer hides
a device.

These tests drive the real discover()/discover_one() with a faked datagram
endpoint, so they exercise the actual probe/collect loop rather than a
reimplementation of it.
"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.jebao_local.jebao_gizwits import discovery as disc  # noqa: E402

FIXTURE = ROOT / "fixtures" / "discovery_reply.bin"
pytestmark = pytest.mark.skipif(
    not FIXTURE.is_file(), reason="needs fixtures/discovery_reply.bin (a real captured reply)"
)


class _FakeTransport:
    """Records probes and lets a test decide which one gets answered."""

    def __init__(self, protocol, reply_on_probe, reply_bytes, reply_ip="10.0.0.5"):
        self._protocol = protocol
        self._reply_on_probe = reply_on_probe
        self._reply_bytes = reply_bytes
        self._reply_ip = reply_ip
        self.probes = 0
        self.closed = False

    def sendto(self, data, addr):
        self.probes += 1
        if self.probes == self._reply_on_probe:
            self._protocol.datagram_received(self._reply_bytes, (self._reply_ip, disc.DISCOVERY_PORT))

    def close(self):
        self.closed = True


@pytest.fixture
def patch_endpoint(monkeypatch):
    created = {}

    def install(reply_on_probe):
        async def fake_create_datagram_endpoint(factory, **kwargs):
            protocol = factory()
            transport = _FakeTransport(protocol, reply_on_probe, FIXTURE.read_bytes())
            created["transport"] = transport
            return transport, protocol

        loop = asyncio.get_event_loop()
        monkeypatch.setattr(loop, "create_datagram_endpoint", fake_create_datagram_endpoint)
        return created

    return install


def test_discover_sends_more_than_one_probe(patch_endpoint):
    created = patch_endpoint(reply_on_probe=99)  # never answers
    devices = asyncio.get_event_loop().run_until_complete(disc.discover(timeout=3.0))
    assert devices == []
    assert created["transport"].probes > 1, "a single broadcast is how devices get missed"
    assert created["transport"].closed


def test_device_answering_only_a_later_probe_is_still_found(patch_endpoint):
    # The whole point: the first probe (or its reply) was lost.
    created = patch_endpoint(reply_on_probe=3)
    devices = asyncio.get_event_loop().run_until_complete(disc.discover(timeout=3.0))
    assert len(devices) == 1
    assert devices[0].did
    assert created["transport"].probes >= 3


def test_probes_stay_within_the_timeout_window(patch_endpoint):
    created = patch_endpoint(reply_on_probe=99)
    asyncio.get_event_loop().run_until_complete(disc.discover(timeout=0.5))
    # Only offsets below the timeout may fire - a short scan must not
    # somehow send every probe anyway.
    expected = sum(1 for o in disc.PROBE_OFFSETS if o < 0.5)
    assert created["transport"].probes == expected


def test_discover_one_stops_early_once_the_device_answers(patch_endpoint):
    created = patch_endpoint(reply_on_probe=1)
    loop = asyncio.get_event_loop()
    started = loop.time()
    device = loop.run_until_complete(disc.discover_one("10.0.0.5", timeout=5.0))
    elapsed = loop.time() - started
    assert device is not None
    # Answered on the first probe, so it must not burn the full 5s window.
    assert elapsed < 2.0, f"took {elapsed:.1f}s - should return as soon as the device replies"
    assert created["transport"].probes == 1
