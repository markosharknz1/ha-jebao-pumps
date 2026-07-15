"""
mitmproxy addon for P0.1: watch for the Jebao app's cleartext datapoint-schema
request and pull the x-gizwits-user-token header out of it automatically.

Usage:
    mitmdump -s scripts/capture_token.py -p 8080
"""
from pathlib import Path

from mitmproxy import http

OUT_PATH = Path(__file__).resolve().parent.parent / "captured_token.txt"


def request(flow: http.HTTPFlow) -> None:
    if flow.request.pretty_host != "usapi.gizwits.com":
        return
    if "/app/datapoint" not in flow.request.path:
        return

    token = flow.request.headers.get("x-gizwits-user-token")
    if not token:
        print(f"[capture_token] Saw datapoint request but no user-token header: {flow.request.path}")
        return

    OUT_PATH.write_text(token, encoding="utf-8")
    print(f"[capture_token] Captured USER_TOKEN, saved to {OUT_PATH}")
    print(f"[capture_token] product_key in request: {flow.request.query.get('product_key')}")
