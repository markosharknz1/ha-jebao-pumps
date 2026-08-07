#!/usr/bin/env python3
"""Fetch a Jebao/Gizwits product datapoint schema and bundle it.

Most bundled schemas came from the vendor app's own `assets/productConfig/`
files. But not every product is cached there - the Local Wavemaker Pro
(`50dbc922...`) turned up on a real user's network with no local copy, so
the app must fetch it from Gizwits at runtime. That endpoint turns out to
be plain HTTP and needs only the app's application-id (no user token, no
TLS interception, no emulator):

    GET http://usapi.gizwits.com/app/datapoint?product_key=<key>
    x-gizwits-application-id: <the Jebao Aqua app's id>

The endpoint and header were captured in Phase 1 (see discovery/findings.md);
this script just replays that request. Validated by fetching a product we
already had a known-good bundled schema for and diffing - byte-for-byte
identical, which is what makes the response trustworthy for a product we
*don't* have.

Usage:
    python tools/fetch_product_schema.py <product_key> [--name-en "Some Pump"]
    python tools/fetch_product_schema.py <product_key> --check   # diff only

Without --check the schema is written into the bundled schemas directory
(refusing to clobber an existing file unless --force).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "custom_components" / "jebao_local" / "jebao_gizwits" / "schemas"

API_URL = "http://usapi.gizwits.com/app/datapoint?product_key={key}"
APPLICATION_ID = "c3703c4888ec4736a3a0d9425c321604"  # the Jebao Aqua app's own id


def fetch(product_key: str, timeout: float = 25.0) -> dict:
    req = urllib.request.Request(
        API_URL.format(key=product_key),
        headers={"x-gizwits-application-id": APPLICATION_ID},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise SystemExit(f"HTTP {resp.status} for {product_key}")
        return json.loads(resp.read().decode("utf-8"))


def to_bundled(raw: dict, name_en: str | None) -> dict:
    """Match the bundled-schema convention: the vendor payload plus this
    project's own name_en field (see jebao_gizwits/schema.py's load())."""
    out = {
        "name": raw["name"],
        "name_en": name_en or raw["name"],
        "packetVersion": raw["packetVersion"],
        "protocolType": raw["protocolType"],
        "product_key": raw["product_key"],
        "entities": raw["entities"],
    }
    if "ui" in raw:
        out["ui"] = raw["ui"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("product_key")
    ap.add_argument("--name-en", help="English product name for the bundled schema")
    ap.add_argument("--check", action="store_true", help="compare against the bundled copy, write nothing")
    ap.add_argument("--force", action="store_true", help="overwrite an existing bundled schema")
    args = ap.parse_args()

    raw = fetch(args.product_key)
    attrs = raw["entities"][0]["attrs"]
    # Vendor product names are Chinese; Windows consoles default to a
    # codepage that can't encode them, so don't let printing crash the run.
    name = raw["name"].encode(sys.stdout.encoding or "utf-8", "replace").decode(
        sys.stdout.encoding or "utf-8", "replace"
    )
    print(f"{name}  protocolType={raw['protocolType']}  attrs={len(attrs)}")

    dest = SCHEMAS_DIR / f"{args.product_key}.json"

    if args.check:
        if not dest.is_file():
            print(f"not bundled: {dest.name}")
            return 1
        have = json.loads(dest.read_text(encoding="utf-8"))
        have_cmp = {k: v for k, v in have.items() if k != "name_en"}
        want_cmp = {k: v for k, v in to_bundled(raw, None).items() if k != "name_en"}
        same = have_cmp == want_cmp
        print("identical to bundled copy:" if same else "DIFFERS from bundled copy:", same)
        return 0 if same else 1

    if dest.is_file() and not args.force:
        raise SystemExit(f"{dest} already exists (use --force to overwrite)")
    if raw["protocolType"] != "standard":
        print(
            f"WARNING: protocolType={raw['protocolType']!r} - this library only "
            "implements the 'standard' (WiFi) encoding; see docs/SUPPORTED_MODELS.md",
            file=sys.stderr,
        )
    dest.write_text(
        json.dumps(to_bundled(raw, args.name_en), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {dest.relative_to(REPO_ROOT)}")
    print("Next: check AutoTimeNN slot length against schedule.py's FIELDS_BY_LEN,")
    print("and run the test suite - per-product counts are asserted there.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
