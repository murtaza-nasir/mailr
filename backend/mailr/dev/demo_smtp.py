"""A local SMTP sink for the demo instance. Development only.

    python -m mailr.dev.demo_smtp [--port 2526] [--dir ./.data-demo/smtp]

The demo accounts send through 127.0.0.1:2526 without authentication; AUTH is offered too (any
login is accepted), so the add-account wizard's connection test passes. Every message is accepted
and written to the directory as an .eml file; nothing is relayed, so a send from the demo can
never reach anyone. Needs aiosmtpd (a development dependency).
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path


class Sink:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    async def handle_DATA(self, server, session, envelope):
        name = self.root / f"{time.time():.6f}.eml"
        name.write_bytes(envelope.original_content or envelope.content)
        print(f"demo-smtp: {envelope.mail_from} -> {', '.join(envelope.rcpt_tos)} ({name.name})", flush=True)
        return "250 Message accepted (demo sink, not delivered)"


def main() -> None:
    from aiosmtpd.controller import Controller
    from aiosmtpd.smtp import AuthResult

    ap = argparse.ArgumentParser(prog="python -m mailr.dev.demo_smtp")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("MAILR_DEMO_SMTP_PORT", "2526")))
    ap.add_argument("--dir", default="./.data-demo/smtp")
    a = ap.parse_args()
    c = Controller(Sink(Path(a.dir)), hostname=a.host, port=a.port, auth_require_tls=False,
                   authenticator=lambda *_: AuthResult(success=True))
    c.start()
    print(f"demo-smtp: listening on {a.host}:{a.port}, writing to {a.dir}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        c.stop()


if __name__ == "__main__":
    main()
