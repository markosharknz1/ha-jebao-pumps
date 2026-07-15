"""
Phase 1A helper: full TCP port sweep against a single host (the pump), since
the coarse candidate-port scan in scan_lan.py found nothing open on it.
"""
import asyncio
import sys

TIMEOUT = 0.8
CONCURRENCY = 512


async def check_port(ip: str, port: int, sem: asyncio.Semaphore) -> int | None:
    async with sem:
        try:
            fut = asyncio.open_connection(ip, port)
            reader, writer = await asyncio.wait_for(fut, timeout=TIMEOUT)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return port
        except Exception:
            return None


async def main(ip: str, start: int, end: int):
    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = [check_port(ip, p, sem) for p in range(start, end + 1)]
    print(f"Scanning {ip} ports {start}-{end} ({len(tasks)} probes)...")
    results = await asyncio.gather(*tasks)
    open_ports = sorted(p for p in results if p)
    if open_ports:
        print(f"\nOpen ports on {ip}: {open_ports}")
    else:
        print(f"\nNo open ports found on {ip} in range {start}-{end}.")


if __name__ == "__main__":
    ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.77"
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    end = int(sys.argv[3]) if len(sys.argv) > 3 else 65535
    asyncio.run(main(ip, start, end))
