"""python -m mailr.mcp [--http] [--host 127.0.0.1] [--port 8765]"""

from __future__ import annotations

import argparse
import asyncio

from mailr.mcp.server import build_server, run_http, run_stdio


def main() -> None:
    ap = argparse.ArgumentParser(prog="mailr-mcp", description="MCP server for a mailr instance")
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP at /mcp instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--url", help="mailr base URL (default $MAILR_URL)")
    a = ap.parse_args()
    server = build_server(url=a.url)
    if a.http:
        run_http(server, a.host, a.port)
    else:
        asyncio.run(run_stdio(server))


if __name__ == "__main__":
    main()
