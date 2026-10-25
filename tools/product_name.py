#!/usr/bin/env python3
"""The product's name in user-facing text: "Mailr" in prose, `mailr` in code.

The name people read is "Mailr". The CLI command, package ids, URL schemes, `MAILR_*` variables,
repo, image, service and container names, paths, identifiers and i18n keys stay `mailr`.

    tools/product_name.py --check     exit 1 when a user-facing text breaks the rule (the backend
                                      test tests/test_product_name.py runs the same check)
    tools/product_name.py --fix       rewrite the prose mentions in place (one-off, then review)

A text is split into prose and code. Code is: inline code (`…`, <code>…</code>), fenced blocks,
URLs and link targets, HTML tags, attribute lists, and a command (`mailr <cli command>`). In prose,
the word `mailr` becomes "Mailr" unless it is part of an identifier, path or address (mailr-cite:,
mailr.db, io.github.murtazanasir.mailr, MAILR_URL, mailr://). The check fails on a lowercase `mailr` word in
prose (a) and on "Mailr" inside code (b).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The CLI's subcommands, read from the parser so a new one is protected at once.
# Optional command modules (backend/mailr/cli_ext) count when they are present.
_CLI_FILES = [ROOT / "backend/mailr/cli.py", *sorted((ROOT / "backend/mailr/cli_ext").glob("*.py"))]
_CLI_TEXT = "\n".join(f.read_text(encoding="utf-8") for f in _CLI_FILES if f.exists())
CLI = sorted(set(re.findall(r'add_parser\(\s*"([\w-]+)"', _CLI_TEXT)), key=len, reverse=True)
# Commands with their own subcommands; "mailr calendars" alone is prose ("your mailr calendars").
GROUPS = {"calendars": ("export", "restore", "migrate", "resync")}
_SUBS = {"export", "restore", "resync"}  # only subcommands ("mailr migrate" is a command too)
for _g, _subs in re.findall(r'CLI_GROUP = \("([\w-]+)", \(([^)]*)\)\)', _CLI_TEXT):
    GROUPS[_g] = tuple(re.findall(r'"([\w-]+)"', _subs))
    _SUBS |= set(GROUPS[_g])
_CMDS = [re.escape(c) for c in CLI if c not in GROUPS and c not in _SUBS]
_CMDS += [rf"{g} (?:{'|'.join(map(re.escape, subs))})" for g, subs in GROUPS.items()]
# "mailr <command>" (and its options) is a command line, never prose.
COMMAND = re.compile(r"\bmailr (?:" + "|".join(_CMDS) + r")(?![\w-])(?:\s+--?[\w-]+(?:[ =][^\s,;.)]+)?)*")
# The word in prose (ASCII neighbours only, so 在mailr中 counts). Not after. / @ - ` $ (addresses, paths, ids) and not before a word character,
# / @ -:/ or.x (mailr-cite:, mailr://, mailr.db, mailr.example.com).
WORD = re.compile(r"(?<![A-Za-z0-9_./@`$-])mailr(?![A-Za-z0-9_/@-]|:/|\.[A-Za-z0-9_])")
CAP = re.compile(r"(?<![A-Za-z0-9_./@`$-])Mailr(?![A-Za-z0-9_/@-]|:/|\.[A-Za-z0-9_])")
# "Mailr" inside code: a word of its own (X-Mailr-Labels, MailrApp and Theme.Mailr are other names).
CODE_CAP = re.compile(r"(?<![A-Za-z0-9_.-])Mailr(?![A-Za-z0-9_-])")

# Inline code and other non-prose pieces of a plain UI string (HTML allowed: <code>, <a href>).
STRING_CODE = re.compile(r"`[^`\n]+`|<code>.*?</code>|<[^>\n]+>|https?://\S+|PRODID:\S+|\bclaude mcp add [^\n]*")
# Pieces of a Markdown line that are not prose.
MD_CODE = re.compile(
    r"(?P<tick>`+)(?:(?!(?P=tick)).)+?(?P=tick)"  # inline code, any number of backticks
    r"|<code>.*?</code>|<kbd>.*?</kbd>"
    r"|\]\([^)\s]*(?:\s+\"[^\"]*\")?\)"  # link and image targets
    r"|^\s*\[[^\]]+\]:\s*\S+"  # reference definitions
    r"|<[^>\n]+>"  # HTML tags and autolinks
    r"|\{[:#.][^}\n]*\}"  # attribute lists
    r"|https?://\S+"
    r"|--8<--.*$"
)
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")

# Kept lowercase on purpose (identifiers that read like prose to the regex). Matched on the text.
ALLOW_LOWER: set[str] = set()
# "Mailr" in code on purpose: the display name in a.desktop file the docs show.
ALLOW_CAP_CODE = re.compile(r"^\s*Name=Mailr$")


def split(text: str, code: re.Pattern[str]) -> Iterator[tuple[bool, str]]:
    """(is_code, piece) for a line or a UI string; commands count as code."""
    pos = 0
    spans = sorted([m.span() for m in code.finditer(text)] + [m.span() for m in COMMAND.finditer(text)])
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    for a, b in merged:
        if a > pos:
            yield False, text[pos:a]
        yield True, text[a:b]
        pos = b
    if pos < len(text):
        yield False, text[pos:]


def fix_string(text: str, code: re.Pattern[str] = STRING_CODE) -> str:
    return "".join(p if is_code else WORD.sub("Mailr", p) for is_code, p in split(text, code))


def problems_string(text: str, code: re.Pattern[str] = STRING_CODE) -> list[str]:
    out = []
    for is_code, p in split(text, code):
        if not is_code and WORD.search(p) and p not in ALLOW_LOWER:
            out.append(f"lowercase in prose: {p.strip()[:100]!r}")
        if is_code and CODE_CAP.search(p):
            out.append(f"'Mailr' in code: {p.strip()[:100]!r}")
    return out


def markdown_lines(text: str) -> Iterator[tuple[int, bool, str]]:
    """(line number, in a fenced block, line)."""
    fence: str | None = None
    prose_fence = False  # a diagram (mermaid) block: its labels are text a reader sees, not code
    for n, line in enumerate(text.split("\n"), 1):
        m = FENCE.match(line)
        if fence is None and m:
            fence = m.group(1)[0] * len(m.group(1))
            prose_fence = line.strip()[len(fence):].strip().lower().startswith("mermaid")
            yield n, True, line
        elif fence is not None:
            if m and m.group(1).startswith(fence):
                fence = None
                yield n, True, line
            else:
                yield n, not prose_fence, line
        else:
            yield n, False, line


def fix_markdown(text: str) -> str:
    return "\n".join(line if fenced else fix_string(line, MD_CODE) for _, fenced, line in markdown_lines(text))


def problems_markdown(text: str) -> list[str]:
    out = []
    for n, fenced, line in markdown_lines(text):
        if fenced:
            if CODE_CAP.search(line) and not FENCE.match(line) and not ALLOW_CAP_CODE.match(line):
                out.append(f"{n}: 'Mailr' in a code block: {line.strip()[:100]!r}")
            continue
        out += [f"{n}: {p}" for p in problems_string(line, MD_CODE)]
    return out


# ---------------------------------------------------------------------------- surfaces

def json_strings(tree: object, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(tree, dict):
        for k, v in tree.items():
            yield from json_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(tree, str):
        yield path, tree


ANDROID_VALUES = sorted(p for mod in ("app", "notes", "notes-core", "session-core")
                        for p in (ROOT / "android" / mod / "src/main/res/values").glob("strings*.xml"))
ANDROID_XML = sorted(p for mod in ("app", "notes") for p in (ROOT / "android" / mod / "src/main/res/xml").glob("*.xml"))
DOCS = sorted((ROOT / "site/docs").rglob("*.md"))
SERVER_EN = ROOT / "backend/mailr/i18n/en.json"
WEB_EN = ROOT / "frontend/src/locales/en.json"
# Single user-visible values outside the catalogues: file, pattern whose group 1 is the text.
SINGLE: list[tuple[str, str]] = [
    ("frontend/index.html", r"<title>([^<]*)</title>"),
    ("frontend/index.html", r'name="application-name" content="([^"]*)"'),
    ("frontend/index.html", r'name="apple-mobile-web-app-title" content="([^"]*)"'),
    ("frontend/index.html", r'name="description" content="([^"]*)"'),
    ("frontend/public/manifest.webmanifest", r'"name": "([^"]*)"'),
    ("frontend/public/manifest.webmanifest", r'"short_name": "([^"]*)"'),
    ("site/mkdocs.yml", r"^site_name: (.*)$"),
    ("site/mkdocs.help.yml", r"^site_name: (.*)$"),
    ("tools/desktop/install-mailto-handler.sh", r"^Name=(.*)$"),
]


def android_strings(path: Path) -> Iterator[tuple[str, str]]:
    root = ET.parse(path).getroot()
    for el in root.iter():
        if el.tag in ("string", "item") and el.text:
            yield el.get("name") or el.tag, el.text
    for el in root.iter():
        for k, v in el.attrib.items():
            if k.endswith(("Label", "label", "description")) and not v.startswith("@"):
                yield f"{el.tag}@{k}", v


def check() -> list[str]:
    out: list[str] = []
    for f in (WEB_EN, SERVER_EN):
        for key, s in json_strings(json.loads(f.read_text(encoding="utf-8"))):
            out += [f"{f.relative_to(ROOT)} {key}: {p}" for p in problems_string(s)]
    for f in ANDROID_VALUES + ANDROID_XML:
        for name, s in android_strings(f):
            out += [f"{f.relative_to(ROOT)} {name}: {p}" for p in problems_string(s)]
    for f in DOCS:
        out += [f"{f.relative_to(ROOT)}:{p}" for p in problems_markdown(f.read_text(encoding="utf-8"))]
    for rel, pat in SINGLE:
        f = ROOT / rel
        for m in re.finditer(pat, f.read_text(encoding="utf-8"), re.M):
            out += [f"{rel}: {p}" for p in problems_string(m.group(1))]
    return out


def rewrite(path: Path, fn: Callable[[str], str]) -> int:
    old = path.read_text(encoding="utf-8")
    new = fn(old)
    if new != old:
        path.write_text(new, encoding="utf-8")
    return len(WORD.findall(old)) - len(WORD.findall(new))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--fix", action="store_true")
    args = ap.parse_args(argv)
    if args.fix:
        for f in DOCS:
            n = rewrite(f, fix_markdown)
            if n:
                print(f"{f.relative_to(ROOT)}: {n}")
        return 0
    probs = check()
    for p in probs:
        print(p)
    print(f"{len(probs)} problems")
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
