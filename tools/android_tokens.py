#!/usr/bin/env python3
"""The web's design tokens as Kotlin, for the Android apps' web look.

Reads the Classic palette, radii and sizes from frontend/src/styles/tokens.css (light and dark), and
the type of a few named web elements (body text, buttons, rows, menus, dialogs, sheets, the tab bar)
from the other style sheets, and writes them to
android/notes-core/src/main/java/io/github/murtazanasir/mailr/look/WebTokens.kt. The Android web-style theme and
its components (io.github.murtazanasir.mailr.look) read only that file, so the two apps cannot drift apart.

    tools/android_tokens.py           write WebTokens.kt
    tools/android_tokens.py --check   change nothing; exit 1 when WebTokens.kt is out of date

tools/generate runs it (and --check under tools/generate --check).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STYLES = ROOT / "frontend/src/styles"
OUT = ROOT / "android/notes-core/src/main/java/io/github/murtazanasir/mailr/look/WebTokens.kt"

# Sizes from tokens.css:root (and phone.css:root), in px = dp.
SIZES = [
    "radius-xs", "radius-sm", "radius-md", "radius-lg", "radius-xl",
    "topbar-h", "sidebar-w", "rail-w", "panel-w", "row-h", "row-h-chips",
    "nav-h", "menu-h", "panel-row-h", "msg-head-pad", "tabbar-h",
]

# Type roles: (style sheet, selector as written there, or several applied in order, as the cascade
# would). font-size, line-height, font-weight and letter-spacing come from those rules; what they
# leave out comes from body (base.css).
TYPE = {
    "body": ("base.css", "body"),
    "button": ("components.css", ".btn"),
    "buttonSmall": ("components.css", [".btn", ".btn.sm"]),
    "fieldLabel": ("components.css", ".field > label,\n.field-label"),
    "fieldHint": ("components.css", ".field .hint"),
    "menuItem": ("components.css", ".menu-item"),
    "menuShortcut": ("components.css", ".menu-item .shortcut"),
    "dialogTitle": ("components.css", ".dialog-head h2"),
    "toast": ("components.css", ".toast"),
    "chip": ("components.css", ".chip"),
    "labelChip": ("components.css", ".label-chip"),
    "pill": ("components.css", ".pill"),
    "badge": ("components.css", ".badge"),
    "sheetTitle": ("phone.css", ".sheet-head h2"),
    "tabLabel": ("phone.css", ".phone-tabs .tab"),
    "tabBadge": ("phone.css", ".tab-badge"),
    "moreRow": ("phone.css", ".more-row"),
    "row": ("list.css", ".conv-row"),
    "rowCount": ("list.css", ".conv-row .sender .count"),
    "rowDate": ("list.css", ".conv-row .tail"),
    "sectionHead": ("list.css", ".section-head"),
    "sectionCount": ("list.css", ".section-head .count"),
    "listFooter": ("list.css", ".list-footer"),
    "catTab": ("list.css", ".cat-tab"),
    "navItem": ("layout.css", ".nav-item"),
    "navCount": ("layout.css", ".nav-item .count"),
    "search": ("layout.css", ".search-box input"),
}

WEIGHTS = {"normal": 400, "bold": 700}


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def top_rules(css: str) -> list[tuple[str, dict[str, str]]]:
    """The rules outside any @-block, in order: (selector, declarations)."""
    css = strip_comments(css)
    rules: list[tuple[str, dict[str, str]]] = []
    i, depth, start, sel = 0, 0, 0, None
    while i < len(css):
        ch = css[i]
        if ch == "{":
            if depth == 0:
                sel = css[start:i].strip()
                body_start = i + 1
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                if sel is not None and not sel.startswith("@"):
                    decls: dict[str, str] = {}
                    for part in css[body_start:i].split(";"):
                        if ":" in part:
                            k, v = part.split(":", 1)
                            decls[k.strip()] = v.strip()
                    rules.append((sel, decls))
                sel = None
                start = i + 1
        i += 1
    return rules


def norm(sel: str) -> str:
    return re.sub(r"\s+", " ", sel.replace(",", ", ")).replace(" ,", ",").strip()


def rule(sheet: str, selector: str) -> dict[str, str]:
    """Every top-level declaration for exactly this selector, later ones winning."""
    want = norm(selector)
    out: dict[str, str] = {}
    for sel, decls in top_rules((STYLES / sheet).read_text()):
        if norm(sel) == want:
            out.update(decls)
    if not out:
        raise SystemExit(f"android_tokens: no rule {selector!r} in {sheet}")
    return out


def blocks(css: str) -> dict[str, dict[str, str]]:
    """Custom properties per selector group, merged across repeats."""
    out: dict[str, dict[str, str]] = {}
    for sel, decls in top_rules(css):
        props = {k: v for k, v in decls.items() if k.startswith("--")}
        if props:
            out.setdefault(norm(sel), {}).update(props)
    return out


def camel(name: str) -> str:
    parts = name.lstrip("-").split("-")
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def colour(value: str) -> str | None:
    v = value.strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{6})", v)
    if m:
        return f"Color(0xFF{m.group(1).upper()})"
    m = re.fullmatch(r"#([0-9a-f]{3})", v)
    if m:
        return "Color(0xFF" + "".join(c * 2 for c in m.group(1)).upper() + ")"
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)", v)
    if m:
        r, g, b = (int(m.group(k)) for k in (1, 2, 3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return f"Color({r}, {g}, {b}, alpha = {round(a * 255)})"
    return None


def palettes() -> tuple[list[str], dict[str, str], dict[str, str]]:
    b = blocks((STYLES / "tokens.css").read_text())
    light = dict(b[norm(":root,\n[data-theme='light']")])
    accents = b[norm(":root,\n[data-theme]")]
    dark_own = b[norm("[data-theme='dark']")]
    names = [n for n in light if colour(light[n])]
    names += [n for n in accents if n not in names]

    def resolve(scheme: dict[str, str]) -> dict[str, str]:
        full = {**scheme, **{k: v for k, v in accents.items() if k not in scheme}}
        out: dict[str, str] = {}
        for n in names:
            v = full.get(n, light.get(n, ""))
            for _ in range(5):
                m = re.fullmatch(r"var\((--[\w-]+)\)", v.strip())
                if not m:
                    break
                v = full.get(m.group(1), light.get(m.group(1), ""))
            c = colour(v)
            if c is None:
                raise SystemExit(f"android_tokens: {n} is not a colour: {v!r}")
            out[n] = c
        return out

    return names, resolve(light), resolve({**light, **dark_own})


def px(v: str) -> float:
    m = re.fullmatch(r"(-?[\d.]+)px", v.strip())
    if not m:
        raise SystemExit(f"android_tokens: not a px value: {v!r}")
    return float(m.group(1))


def num(x: float) -> str:
    return str(int(x)) if x == int(x) else f"{x:g}f"


def type_role(body: dict[str, str], decls: dict[str, str]) -> str:
    size = px(decls.get("font-size", body["font-size"]))
    lh = decls.get("line-height")
    if lh is None or not lh.endswith("px"):
        # The web's text box follows the body's 20/14 ratio when a rule sets no line height.
        line = round(size * px(body["line-height"]) / px(body["font-size"]))
    else:
        line = px(lh)
    w = decls.get("font-weight", "400")
    weight = WEIGHTS.get(w, int(w) if w.isdigit() else 400)
    ls = decls.get("letter-spacing", "0px")
    if ls.endswith("em"):
        spacing = round(float(ls[:-2]) * size, 2)
    else:
        spacing = px(ls) if ls not in ("0", "normal") else 0.0
    return f"WebType({num(size)}.sp, {num(line)}.sp, {weight}, {num(spacing)}.sp)"


def render() -> str:
    names, light, dark = palettes()
    root = blocks((STYLES / "tokens.css").read_text())[":root"]
    root.update(blocks((STYLES / "phone.css").read_text()).get(":root", {}))
    body = rule("base.css", "body")
    lines = [
        "// GENERATED by tools/android_tokens.py from frontend/src/styles (tokens.css and the type of",
        "// named web elements). Do not edit: change the CSS, then run tools/generate.",
        "package io.github.murtazanasir.mailr.look",
        "",
        "import androidx.compose.runtime.Immutable",
        "import androidx.compose.ui.graphics.Color",
        "import androidx.compose.ui.unit.TextUnit",
        "import androidx.compose.ui.unit.dp",
        "import androidx.compose.ui.unit.sp",
        "",
        "/** One scheme of the web's Classic palette (tokens.css), by the web's token names. */",
        "@Immutable",
        "class WebPalette(",
    ]
    lines += [f"    val {camel(n)}: Color," for n in names]
    lines += [")", "", "/** A web text style: size, line height, weight (100–900) and letter spacing. */", "@Immutable",
              "data class WebType(val size: TextUnit, val lineHeight: TextUnit, val weight: Int, val letterSpacing: TextUnit)",
              "", "object WebTokens {"]
    for title, pal in (("Light", light), ("Dark", dark)):
        lines.append(f"    val {title} = WebPalette(")
        lines += [f"        {camel(n)} = {pal[n]}," for n in names]
        lines.append("    )")
    lines.append("")
    lines.append("    // Sizes (tokens.css and phone.css :root), px as dp.")
    for s in SIZES:
        name = camel(s)
        lines.append(f"    val {name[0].upper() + name[1:]} = {num(px(root['--' + s]))}.dp")
    lines.append("")
    lines.append("    // Type of named web elements (sheet: selector).")
    for role, (sheet, sel) in TYPE.items():
        sels = [sel] if isinstance(sel, str) else sel
        decls: dict[str, str] = {}
        for s in sels:
            decls.update(rule(sheet, s))
        lines.append(f"    /** {sheet}: {' + '.join(norm(s) for s in sels)} */")
        lines.append(f"    val {role[0].upper() + role[1:]} = {type_role(body, decls)}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (sys.argv[1:] if argv is None else argv)
    text = render()
    current = OUT.read_text() if OUT.exists() else ""
    if check:
        if current != text:
            print(f"android_tokens: {OUT.relative_to(ROOT)} is out of date (run tools/android_tokens.py)")
            return 1
        return 0
    if current != text:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text)
        print(f"android_tokens: wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
