#!/usr/bin/env python3
"""Generates the label colour schemes from OKLCH parameters.

Run from the repository root: python3 spec/label-colours.py
The output is the canonical data file spec/fixtures/label-colours.json and its three copies:
frontend/src/lib/labelColours.json (the web app), the Android test resource and the Kotlin table
android/.../ui/util/LabelColourData.kt. The copies are compared in the web and Android tests.

Every shade is designed in OKLCH: lightness steps are even across hues, and chroma is reduced
until the colour fits sRGB. Text colours are chosen for a WCAG contrast of at least 4.5:1.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Hue identities. A label keeps its hue and shade when the scheme changes.
HUES = ["red", "orange", "yellow", "green", "teal", "blue", "indigo", "purple", "pink", "grey"]
SHADES = 4

# Each scheme has its own lightness (L) and chroma (C) per shade, for light and for dark themes,
# so a scheme keeps its character in both. Light themes: tints with dark text up to a solid shade 4
# (L below 0.62 takes light text). Dark themes: deep backgrounds with light text, except Pastel,
# whose light tints keep dark text. Chroma is lowered further where sRGB requires.
# hues: id → (angle, name[, chroma share]); Earth's slate and dusk are greyed, Ocean's sand too.
# grey: the chroma of the grey hue, as a share of the scheme's chroma (Earth's stone is warm,
# Ocean's fog is blue).
# The swatch in the label list (a dot beside the name) sits on the page background.
L_DOT_LIGHT = [0.720, 0.660, 0.600, 0.520]
L_DOT_DARK = [0.660, 0.720, 0.780, 0.840]

SCHEMES = [
    {
        "id": "default", "name": "Default",
        "description": "Balanced colours, a little deeper than Gmail's.",
        "light": {"L": [0.952, 0.895, 0.800, 0.560], "C": [0.036, 0.068, 0.110, 0.150]},
        "dark": {"L": [0.370, 0.425, 0.480, 0.535], "C": [0.052, 0.066, 0.082, 0.098]},
        "grey": 0.12,
        "hues": {
            "red": (25, "Red"), "orange": (55, "Orange"), "yellow": (88, "Yellow"), "green": (148, "Green"),
            "teal": (190, "Teal"), "blue": (250, "Blue"), "indigo": (275, "Indigo"), "purple": (310, "Purple"),
            "pink": (355, "Pink"), "grey": (260, "Grey"),
        },
    },
    {
        "id": "muted", "name": "Muted",
        "description": "Greyed colours for a quiet, professional inbox.",
        "light": {"L": [0.955, 0.905, 0.830, 0.600], "C": [0.010, 0.018, 0.028, 0.040]},
        "dark": {"L": [0.400, 0.455, 0.510, 0.565], "C": [0.010, 0.014, 0.019, 0.024]},
        "grey": 0.2,
        "hues": {
            "red": (22, "Brick"), "orange": (55, "Copper"), "yellow": (88, "Straw"), "green": (145, "Moss"),
            "teal": (195, "Spruce"), "blue": (245, "Steel"), "indigo": (275, "Slate"), "purple": (310, "Heather"),
            "pink": (355, "Rosewood"), "grey": (260, "Grey"),
        },
    },
    {
        "id": "vivid", "name": "Vivid",
        "description": "Saturated colours that stand out, in light and dark.",
        "light": {"L": [0.910, 0.830, 0.720, 0.570], "C": [0.080, 0.130, 0.180, 0.220]},
        "dark": {"L": [0.340, 0.410, 0.480, 0.560], "C": [0.115, 0.150, 0.180, 0.200]},
        "grey": 0.08,
        "hues": {
            "red": (27, "Red"), "orange": (52, "Orange"), "yellow": (92, "Yellow"), "green": (145, "Green"),
            "teal": (185, "Teal"), "blue": (255, "Blue"), "indigo": (280, "Indigo"), "purple": (315, "Magenta"),
            "pink": (0, "Pink"), "grey": (260, "Grey"),
        },
    },
    {
        "id": "pastel", "name": "Pastel",
        "description": "Light, airy tints with dark text, in light and dark themes.",
        "light": {"L": [0.965, 0.935, 0.900, 0.860], "C": [0.025, 0.040, 0.055, 0.070]},
        "dark": {"L": [0.900, 0.860, 0.820, 0.780], "C": [0.035, 0.050, 0.065, 0.080], "text": "dark"},
        "grey": 0.15,
        "hues": {
            "red": (20, "Blush"), "orange": (60, "Peach"), "yellow": (95, "Butter"), "green": (140, "Mint"),
            "teal": (185, "Aqua"), "blue": (240, "Sky"), "indigo": (270, "Periwinkle"), "purple": (305, "Lilac"),
            "pink": (350, "Rose"), "grey": (260, "Cloud"),
        },
    },
    {
        "id": "earth", "name": "Earth",
        "description": "Terracotta, ochre, olive, sage and warm stone.",
        "light": {"L": [0.925, 0.845, 0.720, 0.480], "C": [0.042, 0.070, 0.098, 0.115]},
        "dark": {"L": [0.310, 0.370, 0.430, 0.490], "C": [0.055, 0.072, 0.090, 0.105]},
        "grey": 0.25,
        "hues": {
            "red": (40, "Terracotta"), "orange": (64, "Ochre"), "yellow": (86, "Sand", 0.8), "green": (112, "Olive"),
            "teal": (140, "Sage", 0.8), "blue": (215, "Slate", 0.65), "indigo": (255, "Dusk", 0.65),
            "purple": (350, "Plum", 0.8), "pink": (22, "Clay", 0.8), "grey": (70, "Stone"),
        },
    },
    {
        "id": "ocean", "name": "Ocean",
        "description": "Blues, teals and sea greens, with coral and sand.",
        "light": {"L": [0.940, 0.870, 0.760, 0.520], "C": [0.040, 0.070, 0.105, 0.130]},
        "dark": {"L": [0.320, 0.380, 0.440, 0.500], "C": [0.065, 0.085, 0.105, 0.120]},
        "grey": 0.3,
        "hues": {
            "red": (22, "Coral", 0.85), "orange": (75, "Sand", 0.6), "yellow": (128, "Seagrass"), "green": (160, "Kelp"),
            "teal": (185, "Lagoon", 1.1), "blue": (210, "Ocean", 1.1), "indigo": (232, "Deep", 1.1),
            "purple": (252, "Twilight"), "pink": (280, "Urchin"), "grey": (225, "Fog"),
        },
    },
]

# ---------------------------------------------------------------- colour maths


def oklch_to_srgb(L: float, C: float, h: float) -> tuple[float, float, float]:
    a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return r, g, bl


def in_gamut(rgb: tuple[float, float, float]) -> bool:
    return all(-1e-4 <= c <= 1 + 1e-4 for c in rgb)


def encode(c: float) -> int:
    c = min(1.0, max(0.0, c))
    v = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
    return round(v * 255)


def oklch_hex(L: float, C: float, h: float) -> str:
    """The sRGB hex of an OKLCH colour, with chroma lowered until it fits the gamut."""
    lo, hi = 0.0, C
    if not in_gamut(oklch_to_srgb(L, C, h)):
        for _ in range(30):
            mid = (lo + hi) / 2
            if in_gamut(oklch_to_srgb(L, mid, h)):
                lo = mid
            else:
                hi = mid
        C = lo
    r, g, b = oklch_to_srgb(L, C, h)
    return "#{:02x}{:02x}{:02x}".format(encode(r), encode(g), encode(b))


def luminance(hex_: str) -> float:
    def lin(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


AA = 4.5


def text_on(bg: str, h: float, chroma: float, prefer_light: bool, strict: bool = False) -> str | None:
    """A text colour of the same hue that reaches AA on bg: light text or dark text, as preferred,
    stepping lightness away from the background until the ratio holds. With strict, only the
    preferred kind counts (None when it fails)."""
    order = ("light", "dark") if prefer_light else ("dark", "light")
    for kind in order[:1] if strict else order:
        steps = [0.97, 0.98, 0.99, 1.0] if kind == "light" else [0.34, 0.30, 0.26, 0.22, 0.18]
        for L in steps:
            c = min(0.03, chroma * 0.5) if kind == "light" else min(0.09, chroma * 0.6 + (0.035 if chroma > 0.01 else 0))
            fg = oklch_hex(L, c if L < 1 else 0, h)
            if contrast(fg, bg) >= AA + 0.1:
                return fg
    if strict:
        return None
    raise SystemExit(f"no AA text for {bg}")


def solid(L: float, C: float, h: float) -> tuple[str, str]:
    """A background for light text: lightness steps down until light text reaches AA, so a
    luminous hue (green, yellow) gets a deeper solid shade than blue does."""
    while True:
        bg = oklch_hex(L, C, h)
        fg = text_on(bg, h, C, prefer_light=True, strict=True)
        if fg:
            return bg, fg
        L -= 0.01


# ---------------------------------------------------------------- build


def shade(L: float, C: float, h: float, dark_text: bool) -> tuple[str, str]:
    """Background and text: a tint with dark text, or a solid shade with light text."""
    if not dark_text and L < 0.62:
        return solid(L, C, h)
    bg = oklch_hex(L, C, h)
    return bg, text_on(bg, h, C, prefer_light=False)


def build() -> dict:
    schemes = []
    for s in SCHEMES:
        hues = []
        for hid in HUES:
            angle, name, *mul = s["hues"][hid]
            gfac = s["grey"] if hid == "grey" else (mul[0] if mul else 1.0)
            lt, dk = s["light"], s["dark"]
            light, dark = [], []
            for i in range(SHADES):
                C = lt["C"][i] * gfac
                bg, fg = shade(lt["L"][i], C, angle, dark_text=lt["L"][i] >= 0.62)
                dot = oklch_hex(L_DOT_LIGHT[i], max(C, lt["C"][3] * gfac * 0.9), angle)
                light.append({"bg": bg, "fg": fg, "dot": dot})
                Cd = dk["C"][i] * gfac
                bgd, fgd = shade(dk["L"][i], Cd, angle, dark_text=dk.get("text") == "dark")
                dotd = bgd if dk.get("text") == "dark" else oklch_hex(L_DOT_DARK[i], dk["C"][3] * gfac * 1.2, angle)
                dark.append({"bg": bgd, "fg": fgd, "dot": dotd})
            hues.append({"id": hid, "name": name, "light": light, "dark": dark})
        schemes.append({"id": s["id"], "name": s["name"], "description": s["description"], "hues": hues})
    return {
        "comment": "Generated by spec/label-colours.py; edit the script, not this file.",
        "version": 1,
        "shades": SHADES,
        "hues": HUES,
        "schemes": schemes,
    }


def oklab(hex_: str) -> tuple[float, float, float]:
    def lin(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(int(hex_[i:i + 2], 16)) for i in (1, 3, 5))
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def distinctness(data: dict, mode: str) -> dict[tuple[str, str], float]:
    """Mean OKLab distance between two schemes' backgrounds, hue by hue and shade by shade."""
    out = {}
    sc = data["schemes"]
    for i, a in enumerate(sc):
        for b in sc[i + 1:]:
            ds = [math.dist(oklab(x[mode][k]["bg"]), oklab(y[mode][k]["bg"]))
                  for x, y in zip(a["hues"], b["hues"]) for k in range(SHADES)]
            out[(a["id"], b["id"])] = sum(ds) / len(ds)
    return out


def kotlin(data: dict) -> str:
    lines = [
        "// Generated by spec/label-colours.py from spec/fixtures/label-colours.json; do not edit.",
        "// This table is compared with the JSON copy in the test resources in LabelColourDataTest.",
        "package io.github.murtazanasir.mailr.ui.util",
        "",
        "internal object LabelColourData {",
        f"    const val SHADES = {data['shades']}",
        "    val HUES = listOf(" + ", ".join(f'"{h}"' for h in data["hues"]) + ")",
        "",
        "    /** One shade: background, text and the list swatch, for light and for dark themes. */",
        "    class Shade(val bg: String, val fg: String, val dot: String, val darkBg: String, val darkFg: String, val darkDot: String)",
        "    class Hue(val id: String, val name: String, val shades: List<Shade>)",
        "    class Scheme(val id: String, val name: String, val hues: List<Hue>)",
        "",
        "    val SCHEMES: List<Scheme> = listOf(",
    ]
    for s in data["schemes"]:
        lines.append(f'        Scheme("{s["id"]}", "{s["name"]}", listOf(')
        for h in s["hues"]:
            shades = ", ".join(
                f'Shade("{lt["bg"]}", "{lt["fg"]}", "{lt["dot"]}", "{dk["bg"]}", "{dk["fg"]}", "{dk["dot"]}")'
                for lt, dk in zip(h["light"], h["dark"]))
            lines.append(f'            Hue("{h["id"]}", "{h["name"]}", listOf({shades})),')
        lines.append("        )),")
    lines += ["    )", "}", ""]
    return "\n".join(lines)


def main() -> None:
    data = build()
    text = json.dumps(data, indent=1) + "\n"
    worst = min(contrast(sh["fg"], sh["bg"]) for s in data["schemes"] for h in s["hues"]
                for mode in ("light", "dark") for sh in h[mode])
    for rel in ("spec/fixtures/label-colours.json", "frontend/src/lib/labelColours.json",
                "android/app/src/test/resources/design/label-colours.json"):
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (ROOT / "android/app/src/main/java/io/github/murtazanasir/mailr/ui/util/LabelColourData.kt").write_text(kotlin(data))
    print(f"{len(data['schemes'])} schemes x {len(HUES)} hues x {SHADES} shades; lowest text contrast {worst:.2f}")
    for mode in ("light", "dark"):
        d = distinctness(data, mode)
        low = min(d, key=d.get)
        print(f"{mode}: mean OKLab distance between schemes {sum(d.values()) / len(d):.3f}, closest pair {low} {d[low]:.3f}")
        print("   " + "  ".join(f"{a[:3]}-{b[:3]} {v:.3f}" for (a, b), v in sorted(d.items(), key=lambda kv: kv[1])))


if __name__ == "__main__":
    main()
