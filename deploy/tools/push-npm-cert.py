#!/usr/bin/env python3
"""Copy a Let's Encrypt certificate from Nginx Proxy Manager into mailr (for IMAP TLS).

NPM renews the certificate; run this daily (cron/systemd timer) and mailr installs the new
files into Stalwart only when they changed. Standard library only.

Environment (e.g. from an env file with chmod 600):
  NPM_URL        http://npm-host:81
  NPM_USER       an NPM user limited to *viewing certificates* (visibility "all",
                 certificates "view", everything else hidden). Not the NPM admin.
  NPM_PASSWORD   its password
  NPM_CERT_ID    id of the certificate in NPM (a wildcard like *.example.com works)
  MAILR_URL      http://mailr-host:8025
  MAILR_TOKEN    mailr API token with only the `tls` scope (an admin's token)

Usage: push-npm-cert.py [ENV_FILE]   (KEY=value lines; values are taken literally)
"""

import io
import json
import os
import sys
import urllib.request
import zipfile


def req(url, data=None, headers=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, method=method,
                               headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return resp.read()


def load_env(path: str) -> dict:
    out = {}
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v
    return out


def main() -> int:
    e = {**os.environ, **(load_env(sys.argv[1]) if len(sys.argv) > 1 else {})}
    npm = e["NPM_URL"].rstrip("/")
    tok = json.loads(req(f"{npm}/api/tokens", {"identity": e["NPM_USER"], "secret": e["NPM_PASSWORD"]}))["token"]
    z = zipfile.ZipFile(io.BytesIO(req(f"{npm}/api/nginx/certificates/{e['NPM_CERT_ID']}/download",
                                       headers={"Authorization": f"Bearer {tok}"})))
    names = z.namelist()
    chain = next(n for n in names if n.startswith("fullchain"))
    key = next(n for n in names if n.startswith("privkey"))
    out = req(f"{e['MAILR_URL'].rstrip('/')}/api/v1/admin/tls",
              {"certificate": z.read(chain).decode(), "private_key": z.read(key).decode()},
              headers={"Authorization": f"Bearer {e['MAILR_TOKEN']}"}, method="PUT")
    info = json.loads(out)
    print(f"{'installed' if info.get('changed') else 'unchanged'}: {info.get('names') or info.get('subject')} "
          f"expires {info.get('not_after')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
