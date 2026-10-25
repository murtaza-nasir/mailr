"""Shared helpers, from test_egress.py."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class _Proxy:
    """A local HTTP proxy stand-in: answers absolute-URI GETs from `routes` (path -> (status,
    headers, body)) and records every request line, CONNECTs included (refused with 502)."""

    def __init__(self, routes: dict[str, tuple[int, dict, bytes]] | None = None):
        self.routes = routes or {}
        self.seen: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.seen.append(f"GET {self.path}")
                u = urlsplit(self.path)
                status, headers, body = outer.routes.get(u.netloc + u.path, (404, {}, b"no"))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_CONNECT(self):
                outer.seen.append(f"CONNECT {self.path}")
                self.send_response(502)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
