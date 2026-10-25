#!/usr/bin/env bash
# WebKit for the phone tests (playwright.config.ts, project "iphone"), in Playwright's own
# container: WebKit's Linux build needs libraries (ICU 74, libxml2 2.x, flite) many hosts lack.
#   frontend/scripts/webkit-server.sh start [port]   # prints E2E_WEBKIT_WS=ws://127.0.0.1:<port>/
#   frontend/scripts/webkit-server.sh stop
# The container uses the host network, so it reaches the dev servers on localhost.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(node -p "require('@playwright/test/package.json').version")
NAME="mailr-pw-webkit-$(basename "$(cd .. && pwd)")"
PORT="${2:-3917}"
case "${1:-start}" in
  start)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker run -d --rm --name "$NAME" --network host --init --ipc host \
      "mcr.microsoft.com/playwright:v${VERSION}-noble" \
      /bin/sh -c "cd /tmp && npx -y playwright@${VERSION} run-server --port ${PORT} --host 127.0.0.1" >/dev/null
    for _ in $(seq 1 60); do
      if (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; then echo "E2E_WEBKIT_WS=ws://127.0.0.1:${PORT}/"; exit 0; fi
      sleep 1
    done
    echo "the WebKit server did not start; docker logs $NAME" >&2
    exit 1
    ;;
  stop)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    ;;
esac
