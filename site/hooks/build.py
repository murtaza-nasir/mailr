"""MkDocs hooks for every build of site/docs (the public site and /help/, mkdocs.help.yml).

- `<!-- covers: … -->` markers in the Markdown are for the docs coverage check; they are taken out
  of the built pages.
- Phone-sized screenshots are given a width from their pixel size and the scale they were taken
  at, so a phone screen is shown at about a phone's width on every page and the wider pictures (a
  fold, a row of widgets) proportionally wider. Which shots are phone-sized comes from
  site/shots.yaml (Android shots, and web shots at phone width), not from their file names. An
  image with its own attribute list keeps it.
- Mermaid is served from the site itself (assets/vendor/mermaid.min.js, listed in extra_javascript),
  so no page loads it from a CDN. The file is the pinned, checksummed copy of build_help.py:
  MAILR_DOCS_MERMAID (a verified file) when set, else downloaded once into ~/.cache/mailr/.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

COVERS = re.compile(r"<!--\s*covers:.*?-->\n?", re.DOTALL)
SHOT = re.compile(r"(!\[[^\]]*\]\(([^)\s]*assets/shots/([\w-]+)\.png)\))(?!\{)")
# Pixels per CSS pixel: Android shots are taken at about 3.4, web shots at phone width at 2 (3 for
# the iPhone recipes, whose names start with "pwa-"; frontend/e2e-docs/harness.ts).
ANDROID_SCALE = 3.4
_scales: dict[str, float] | None = None


def _phone_scales(config) -> dict[str, float]:
    """Shot name -> its scale, for the phone-sized shots in site/shots.yaml."""
    global _scales
    if _scales is None:
        import yaml

        out: dict[str, float] = {}
        data = yaml.safe_load((Path(config["config_file_path"]).parent / "shots.yaml").read_text()) or {}
        for e in data.get("shots") or []:
            name, platform, viewport = e.get("name"), e.get("platform"), str(e.get("viewport") or "")
            if platform == "android":
                out[name] = ANDROID_SCALE
            elif platform == "web" and viewport == "phone":
                out[name] = 3.0 if name.startswith("pwa-") else 2.0
        _scales = out
    return _scales


def _png_width(path: Path) -> int | None:
    try:
        head = path.read_bytes()[:24]
    except OSError:
        return None
    return int.from_bytes(head[16:20], "big") if head[:8] == b"\x89PNG\r\n\x1a\n" else None


def on_page_markdown(markdown: str, page=None, config=None, **_kw) -> str:
    markdown = COVERS.sub("", markdown)
    if page is None or config is None:
        return markdown
    here = (Path(config["docs_dir"]) / page.file.src_path).parent
    scales = _phone_scales(config)

    def sized(m: re.Match) -> str:
        scale = scales.get(m.group(3))
        px = _png_width(here / m.group(2)) if scale else None
        return f'{m.group(1)}{{ width="{round(px / scale)}" }}' if px else m.group(0)

    return SHOT.sub(sized, markdown)


def on_post_page(output: str, **_kw) -> str:
    return COVERS.sub("", output)


def on_post_build(config, **_kw) -> None:
    import os

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from build_help import mermaid

    lib = mermaid(os.environ.get("MAILR_DOCS_MERMAID") or None)
    vendor = Path(config["site_dir"]) / "assets" / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(lib, vendor / "mermaid.min.js")
