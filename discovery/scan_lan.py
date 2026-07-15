"""
Phase 1A helper: sweep the local /24 subnet for hosts with any of a set of
candidate ports open. Used to locate Jebao pump IPs without relying on the
router's DHCP client list.

Candidate ports:
  80, 443        - common device HTTP/HTTPS admin or status endpoints
  12416          - legacy Gizwits LAN control port (per chrisc123 repo)
  6668, 8888     - other ports seen in some Gizwits/Tuya-family LAN protocols
  9999           - occasionally used by IoT LAN discovery/control

This is a coarse net: any open port gets reported so a human can cross-check
against the router's device list / MAC vendor prefix.
"""
import asyncio
import ipaddress
import sys

CANDIDATE_PORTS = [80, 443, 12416, 6668, 8888, 9999]
TIMEOUT = 0.6
CONCURRENCY = 256


async def check_port(ip: str, port: int, sem: asyncio.Semaphore) -> tuple[str, int] | None:
    async with sem:
        try:
            fut = asyncio.open_connection(ip, port)
            reader, writer = await asyncio.wait_for(fut, timeout=TIMEOUT)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return (ip, port)
        except Exception:
            return None


async def main(subnet: str):
    net = ipaddress.ip_network(subnet, strict=False)
    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = [
        check_port(str(ip), port, sem)
        for ip in net.hosts()
        for port in CANDIDATE_PORTS
    ]
    print(f"Scanning {net} on ports {CANDIDATE_PORTS} ({len(tasks)} probes)...")
    results = await asyncio.gather(*tasks)
    hits: dict[str, list[int]] = {}
    for r in results:
        if r:
            ip, port = r
            hits.setdefault(ip, []).append(port)

    if not hits:
        print("No open candidate ports found.")
        return

    print("\nHosts with open candidate ports:")
    for ip in sorted(hits, key=lambda x: tuple(map(int, x.split(".")))):
        print(f"  {ip}: {sorted(hits[ip])}")


if __name__ == "__main__":
    subnet = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.0/24"
    asyncio.run(main(subnet))
