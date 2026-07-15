"""
P0.1: fetch the Gizwits datapoint schema for this pump's product_key.
This JSON is the single source of truth for what every byte/bit means -
nothing downstream should hardcode a byte meaning that didn't come from here.

Usage:
    Put JEBAO_USER_TOKEN in C:\\jebao-ha\\.env (see .env.example), then:
        python scripts/fetch_datapoint_schema.py
"""
import json
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRODUCT_KEY = "54114ccdac1e41c0bb17e222887c07ba"
APPLICATION_ID = "c3703c4888ec4736a3a0d9425c321604"


def load_env():
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def main():
    load_env()
    user_token = os.environ.get("JEBAO_USER_TOKEN")
    if not user_token:
        raise SystemExit(
            "JEBAO_USER_TOKEN not set. Capture it from the app (see CLAUDE.md) "
            "and put it in C:\\jebao-ha\\.env as JEBAO_USER_TOKEN=<value>."
        )

    url = f"http://usapi.gizwits.com/app/datapoint?product_key={PRODUCT_KEY}"
    req = urllib.request.Request(
        url,
        headers={
            "x-gizwits-application-id": APPLICATION_ID,
            "x-gizwits-user-token": user_token,
        },
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        status = resp.status
        body = resp.read()

    if status != 200:
        raise SystemExit(f"Unexpected status {status}: {body[:500]!r}")

    data = json.loads(body)
    out_path = ROOT / "fixtures" / "datapoint_schema.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved datapoint schema to {out_path} ({len(body)} bytes)")


if __name__ == "__main__":
    main()
