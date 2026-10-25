"""Build the user guide for /help/ inside mailr (backend/mailr/core/helpdocs.py).

    python build_help.py --out DIR [--mermaid FILE] [--mkdocs PATH]

1. mkdocs build --strict with mkdocs.help.yml: mkdocs.yml with relative links, no web fonts, no
   GitHub facts and no instant loading.
2. Mermaid (the theme's diagram library) goes to assets/vendor/mermaid.min.js, checked against
   MERMAID_SHA256: from --mermaid, or downloaded once into ~/.cache/mailr/.
3. 404.html names its files from the site root ("/assets/…"); they are moved under /help/.
4. sitemap.xml, sitemap.xml.gz and source maps are removed: nothing lists the private pages.

Only site/docs is built (docs_dir in mkdocs.yml): the internal docs under docs/ are never part of it.
The image runs this in its docs stage (Dockerfile); a checkout runs it through tools/build-help."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Keep in step with the Dockerfile's docs stage (backend/tests/test_helpdocs.py checks).
MERMAID_VERSION = "11.17.2"
MERMAID_SHA256 = "581ed7d74bd9048d0e3a91363927d72ef22942d7722546b27f7cc29e35390eb8"
MERMAID_URL = f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.min.js"
PREFIX = "/help/"


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def mermaid(given: str | None) -> Path:
    if given:
        p = Path(given)
    else:
        p = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "mailr" / f"mermaid-{MERMAID_VERSION}.min.js"
        if not p.is_file() or _sha256(p) != MERMAID_SHA256:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".part")
            with urllib.request.urlopen(MERMAID_URL, timeout=60) as r, open(tmp, "wb") as f:  # noqa: S310  fixed https URL
                shutil.copyfileobj(r, f)
            tmp.replace(p)
    if _sha256(p) != MERMAID_SHA256:
        sys.exit(f"{p}: not the expected Mermaid {MERMAID_VERSION} (checksum differs)")
    return p


def fix_404(out: Path) -> None:
    """MkDocs writes 404.html for the site root; under /help/ its files are /help/…"""
    f = out / "404.html"
    if not f.is_file():
        return
    html = f.read_text(encoding="utf-8")
    html = re.sub(r'(\b(?:href|src)=")/(?!/)', r"\1" + PREFIX, html)
    html = html.replace('"base": "/"', f'"base": "{PREFIX.rstrip("/")}"')
    html = html.replace('"search": "/assets/', f'"search": "{PREFIX}assets/')
    html = html.replace('new URL("/",location)', f'new URL("{PREFIX}",location)')
    f.write_text(html, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--mermaid")
    ap.add_argument("--mkdocs", default=shutil.which("mkdocs") or "mkdocs")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    lib = mermaid(a.mermaid)
    tmp = out.with_name(out.name + ".building")
    shutil.rmtree(tmp, ignore_errors=True)
    # No page analytics in the private copy, whatever the environment says.
    env = {k: v for k, v in os.environ.items() if not k.startswith("MAILR_DOCS_ANALYTICS_")}
    env["NO_MKDOCS_2_WARNING"] = "1"
    env["MAILR_DOCS_MERMAID"] = str(lib)  # the hook (hooks/build.py) places this verified copy
    subprocess.run([a.mkdocs, "build", "--strict", "-f", str(HERE / "mkdocs.help.yml"), "-d", str(tmp)],
                   cwd=HERE, env=env, check=True)
    vendor = tmp / "assets" / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(lib, vendor / "mermaid.min.js")
    fix_404(tmp)
    for p in [tmp / "sitemap.xml", tmp / "sitemap.xml.gz", *tmp.rglob("*.map")]:
        p.unlink(missing_ok=True)
    # Swap in one step, so a running server never serves half a build.
    old = out.with_name(out.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if out.exists():
        out.rename(old)
    tmp.rename(out)
    shutil.rmtree(old, ignore_errors=True)
    print(f"help pages: {out}")


if __name__ == "__main__":
    main()
