"""
Phase 1A: replicate the Gizwits UDP broadcast discovery from the chrisc123
reference repo (custom_components/jebao_aqua/discovery.py) against the real
network, to see whether the newer ESP32C3 pump responds to it at all.
"""
import asyncio
import socket
import logging

logging.basicConfig(level=logging.DEBUG)
_LOGGER = logging.getLogger(__name__)

BROADCAST_PORT = 12414
BROADCAST_PAYLOAD = b"\x00\x00\x00\x03\x03\x00\x00\x03"
DISCOVERY_TIMEOUT = 8


class DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None
        self.results = {}

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        print(f"\n>>> RAW RESPONSE from {addr}: {data!r} (len={len(data)})")
        if len(data) >= 32:
            device_id_start = 10
            device_id_length = 22
            device_id = (
                data[device_id_start : device_id_start + device_id_length]
                .decode(errors="ignore")
                .strip()
            )
            if device_id:
                self.results[device_id] = addr[0]
                print(f">>> Parsed device_id={device_id!r} at {addr[0]}")


async def discover_devices():
    loop = asyncio.get_event_loop()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", 0))
    source_port = sock.getsockname()[1]
    sock.close()

    protocol = DiscoveryProtocol()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol,
        local_addr=("0.0.0.0", source_port),
        allow_broadcast=True,
    )

    try:
        print(f"Sending broadcast {BROADCAST_PAYLOAD!r} to 255.255.255.255:{BROADCAST_PORT} from local port {source_port}")
        transport.sendto(BROADCAST_PAYLOAD, ("255.255.255.255", BROADCAST_PORT))
        # Also try directed at the known pump IP directly, in case broadcast is filtered
        transport.sendto(BROADCAST_PAYLOAD, ("192.168.1.77", BROADCAST_PORT))
        print(f"Also sent directed unicast to 192.168.1.77:{BROADCAST_PORT}")

        await asyncio.sleep(DISCOVERY_TIMEOUT)

        print(f"\nDiscovery complete. Found devices: {protocol.results}")
        return protocol.results
    finally:
        transport.close()


if __name__ == "__main__":
    asyncio.run(discover_devices())
