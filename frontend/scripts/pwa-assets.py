#!/usr/bin/env python3
"""The installed app's images, from the app icon (public/icon-512.png and the vector mark):

- Apple touch icons (152, 167, 180) and a 192 px maskable icon for the manifest;
- shortcut icons (Compose, Calendar, Tasks: Lucide glyphs on the icon's tile);
- iOS start-up (splash) images for every current iPhone (portrait) and iPad (both
  orientations), light and dark, plus the <link> tags for index.html (printed, and written
  to scripts/pwa-splash-links.html).

    backend/.venv/bin/python frontend/scripts/pwa-assets.py

Needs Pillow and rsvg-convert. The output is committed; run again when the icon changes.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

from PIL import Image

FRONTEND = Path(__file__).resolve().parents[1]
PUB = FRONTEND / "public"
SPLASH = PUB / "splash"
MARK_SVG = FRONTEND.parent / "design" / "icon-concepts" / "stack_check_blue.svg"

LIGHT_BG = "#f4f8f9"  # --bg, Classic light (styles/tokens.css)
DARK_BG = "#1b1b1b"  # --bg, dark
TILE = "#E8F0FE"  # the maskable icon's tile

# Portrait CSS size and pixel ratio. iPhones start in portrait; iPads in either orientation.
IPHONES = [
    (440, 956, 3), (402, 874, 3), (430, 932, 3), (393, 852, 3), (428, 926, 3), (390, 844, 3),
    (375, 812, 3), (414, 896, 3), (414, 896, 2), (414, 736, 3), (375, 667, 2), (320, 568, 2),
]
IPADS = [
    (1032, 1376, 2), (1024, 1366, 2), (834, 1210, 2), (834, 1194, 2), (820, 1180, 2),
    (834, 1112, 2), (810, 1080, 2), (768, 1024, 2), (744, 1133, 2),
]


def mark(px: int) -> Image.Image:
    """The vector mark rendered at px (square, transparent), trimmed to its drawing."""
    png = subprocess.run(["rsvg-convert", "-w", str(px), "-h", str(px), str(MARK_SVG)], check=True, capture_output=True).stdout
    art = Image.open(io.BytesIO(png)).convert("RGBA")
    return art.crop(art.getchannel("A").getbbox())


def placed(canvas: tuple[int, int], art: Image.Image, frac: float, bg: str | None) -> Image.Image:
    w, h = canvas
    out = Image.new("RGBA", canvas, bg or (0, 0, 0, 0))
    s = frac * min(w, h) / max(art.size)
    a = art.resize((round(art.width * s), round(art.height * s)), Image.LANCZOS)
    out.alpha_composite(a, ((w - a.width) // 2, (h - a.height) // 2))
    return out


def save_small(img: Image.Image, path: Path) -> None:
    """Flat backgrounds and one icon: a 128-colour palette keeps each file small."""
    img.convert("RGB").quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(path, optimize=True)


GLYPHS = {
    "compose": '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>',
    "calendar": '<path d="M8 2v3"/><path d="M16 2v3"/><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/>'
                '<path d="M8 13h.01"/><path d="M12 13h.01"/><path d="M16 13h.01"/><path d="M8 17h.01"/><path d="M12 17h.01"/><path d="M16 17h.01"/>',
    "tasks": '<circle cx="12" cy="12" r="10"/><path d="m16 9-5.5 5.5L8 12"/>',
}


def shortcut(name: str, px: int = 96) -> Image.Image:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{px}" height="{px}" viewBox="-6 -6 36 36">'
           f'<rect x="-6" y="-6" width="36" height="36" rx="8" fill="{TILE}"/>'
           f'<g fill="none" stroke="#1f6c86" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{GLYPHS[name]}</g></svg>')
    png = subprocess.run(["rsvg-convert", "-w", str(px), "-h", str(px)], input=svg.encode(), check=True, capture_output=True).stdout
    return Image.open(io.BytesIO(png)).convert("RGBA")


def main() -> None:
    art = mark(2048)
    # Apple touch icons: the mark on its light tile, as the 180 px one already is.
    for px in (152, 167, 180):
        placed((px, px), art, 0.74, TILE).convert("RGB").save(PUB / ("apple-touch-icon.png" if px == 180 else f"apple-touch-icon-{px}.png"), optimize=True)
    placed((192, 192), art, 0.62, TILE).convert("RGB").save(PUB / "icon-maskable-192.png", optimize=True)
    for name in GLYPHS:
        shortcut(name).save(PUB / f"shortcut-{name}.png", optimize=True)

    SPLASH.mkdir(exist_ok=True)
    for old in SPLASH.glob("*.png"):
        old.unlink()
    links = []
    devices = [(w, h, r, "portrait") for w, h, r in IPHONES] + [(w, h, r, o) for w, h, r in IPADS for o in ("portrait", "landscape")]
    for w, h, r, orient in devices:
        pw, ph = (w * r, h * r) if orient == "portrait" else (h * r, w * r)
        for scheme, bg in (("light", LIGHT_BG), ("dark", DARK_BG)):
            name = f"splash-{pw}x{ph}-{scheme}.png"
            # The icon a little under a third of the short side, as iOS's own launch screens.
            save_small(placed((pw, ph), art, 0.28, bg), SPLASH / name)
            media = (f"(device-width: {w}px) and (device-height: {h}px) and (-webkit-device-pixel-ratio: {r}) "
                     f"and (orientation: {orient}) and (prefers-color-scheme: {scheme})")
            links.append(f'    <link rel="apple-touch-startup-image" media="{media}" href="/splash/{name}" />')
    (FRONTEND / "scripts" / "pwa-splash-links.html").write_text("\n".join(links) + "\n")
    total = sum(p.stat().st_size for p in SPLASH.glob("*.png"))
    print(f"{len(links)} splash images, {total / 1024:.0f} kB; links in scripts/pwa-splash-links.html")


if __name__ == "__main__":
    main()
