#!/usr/bin/env python3
"""A stand-in model server for the load test (internal; tools/loadtest/run.py starts it).

OpenAI-compatible /v1/chat/completions and /v1/embeddings with a fixed latency each, and at most
--slots requests served at once (a GPU that runs a few requests at a time; the rest wait in
arrival order). Chat answers are an empty JSON object, which triage reads as "nothing found".
Standard library only.

    python3 tools/loadtest/stub_model.py --port 0 --chat-latency 0.3 --embed-latency 0.05 --slots 4
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--chat-latency", type=float, default=0.3)
    ap.add_argument("--embed-latency", type=float, default=0.05)
    ap.add_argument("--slots", type=int, default=4)
    a = ap.parse_args()
    gpu = threading.Semaphore(a.slots)
    counts = {"chat": 0, "embed": 0, "peak": 0, "now": 0}
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # quiet
            pass

        def _send(self, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            if self.path.endswith("/stats"):
                self._send(dict(counts))
            else:
                self._send({"data": [{"id": "stub"}, {"id": "stub-embed"}]})

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            with gpu:
                with lock:
                    counts["now"] += 1
                    counts["peak"] = max(counts["peak"], counts["now"])
                try:
                    if self.path.endswith("/embeddings"):
                        texts = body.get("input") or []
                        dim = int(body.get("dimensions") or 512)
                        time.sleep(a.embed_latency)
                        counts["embed"] += 1
                        data = []
                        for i, _t in enumerate(texts if isinstance(texts, list) else [texts]):
                            v = [random.random() for _ in range(dim)]
                            n = sum(x * x for x in v) ** 0.5
                            data.append({"index": i, "embedding": [x / n for x in v]})
                        self._send({"data": data, "model": body.get("model")})
                    else:
                        time.sleep(a.chat_latency)
                        counts["chat"] += 1
                        self._send({"choices": [{"index": 0, "finish_reason": "stop",
                                                 "message": {"role": "assistant", "content": "{}"}}],
                                    "model": body.get("model")})
                finally:
                    with lock:
                        counts["now"] -= 1

    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    srv.daemon_threads = True
    print(f"PORT {srv.server_address[1]}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
