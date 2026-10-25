"""Route code raises coded errors.

An error a person can read is `ApiError(status, "area.code", **params)`, so each app shows it in its
own language. A plain `HTTPException(status, "literal text")` in route code fails here, unless the
text is for developers only and the line says so with `# i18n: internal` (on the call's lines or
the line above it).

Route code is every module under mailr/api/, every module that makes an APIRouter, and main.py.
Text built at run time (`str(e)`, a variable) is not checked: it comes from another module.
"""

import ast
import io
import json
import re
import tokenize
from pathlib import Path

import pytest

from mailr import i18n

CODE = Path(i18n.__file__).parent.parent
MARK = "i18n: internal"

# Route files another branch converts (import texts, 7 Oct 2026). Remove an entry once it lands.
PENDING: set[str] = set()


def route_modules() -> list[Path]:
    out = []
    for p in sorted(CODE.rglob("*.py")):
        rel = p.relative_to(CODE).as_posix()
        if rel.startswith(("dev/", "i18n/")) or rel in PENDING:
            continue
        if rel.startswith("api/") or rel == "main.py" or "APIRouter(" in p.read_text():
            out.append(p)
    return out


def _literal(node: ast.expr | None) -> bool:
    """A text written into the code: "…", f"…", or one of those cut or joined."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Subscript):
        return _literal(node.value)
    if isinstance(node, ast.BinOp):
        return _literal(node.left) or _literal(node.right)
    return False


def literal_raises(src: str) -> list[tuple[int, str]]:
    """(line, text) of each HTTPException with a literal detail and no `# i18n: internal` mark."""
    tree = ast.parse(src)
    marked = {t.start[0] for t in tokenize.generate_tokens(io.StringIO(src).readline)
              if t.type == tokenize.COMMENT and MARK in t.string}
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "HTTPException":
            continue
        detail = node.args[1] if len(node.args) > 1 else next(
            (k.value for k in node.keywords if k.arg == "detail"), None)
        if not _literal(detail):
            continue
        if marked & set(range(node.lineno - 1, node.end_lineno + 1)):
            continue
        found.append((node.lineno, ast.get_source_segment(src, detail)[:80]))
    return found


def test_the_check_finds_a_literal_text():
    """Guards the next test from passing while checking nothing."""
    src = (
        'from fastapi import HTTPException\n'
        'def a():\n    raise HTTPException(404, "thing not found")\n'
        'def b(x):\n    raise HTTPException(400, f"bad {x}")\n'
        'def c():\n    raise HTTPException(status_code=409, detail="taken")\n'
        'def d():\n    raise HTTPException(400, "spec must be JSON")  # i18n: internal\n'
        'def e(err):\n    raise HTTPException(400, str(err))\n'
        'def f():\n    # i18n: internal\n    raise HTTPException(400,\n                        "a b c")\n'
    )
    assert [line for line, _ in literal_raises(src)] == [3, 5, 7]
    assert len(route_modules()) > 50


def test_route_errors_carry_a_code():
    bad = []
    for p in route_modules():
        for line, text in literal_raises(p.read_text()):
            bad.append(f"  {p.relative_to(CODE)}:{line}  {text}")
    assert not bad, (
        "HTTPException with English text in route code. Raise ApiError(status, \"area.code\") with "
        "the text under errors.<area>.<code> in mailr/i18n/en.json, or, for a developer-only text, "
        "end the line with `# i18n: internal`:\n" + "\n".join(bad))


def test_pending_files_still_exist():
    for rel in PENDING:
        assert (CODE / rel).exists(), f"{rel} is gone: remove it from PENDING"


# ---------------------------------------------------------------- a converted error in another language

ACCENTS = dict(zip("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",  # noqa: B905
                   "áƀçðéƒĝĥíĵķļɱñóƥʠŕšţúṽŵẋýžÁƁÇÐÉƑĜĤÍĴĶĻṀÑÓƤǪŔŠŢÚṼŴẊÝŽ"))
_PARTS = re.compile(r"(\{\{\s*\w+\s*\}\})")


def pseudo(text: str) -> str:
    """The web's en-XA (frontend/src/i18n/index.ts `pseudo`): accented, in brackets, placeholders kept."""
    parts = _PARTS.split(text)
    body = "".join(p if i % 2 else "".join(ACCENTS.get(c, c) for c in p) for i, p in enumerate(parts))
    letters = sum(c.isascii() and c.isalpha() for i, p in enumerate(parts) if not i % 2 for c in p)
    return f"[{body} {'•' * max(1, int(letters * 0.35 + 0.5))}]"


@pytest.fixture
def pseudo_catalogue(tmp_path, monkeypatch):
    """The real English catalogue and an en-XA one made from it, in a folder of their own."""
    en = json.loads((Path(i18n.__file__).parent / "en.json").read_text(encoding="utf-8"))

    def walk(node):
        return {k: walk(v) if isinstance(v, dict) else pseudo(v) for k, v in node.items()}

    (tmp_path / "en.json").write_text(json.dumps(en), encoding="utf-8")
    (tmp_path / "en-XA.json").write_text(json.dumps({"errors": walk(en["errors"])}), encoding="utf-8")
    monkeypatch.setattr(i18n, "HERE", tmp_path)
    i18n._cache.clear()
    yield
    i18n._cache.clear()


async def test_a_converted_error_reads_in_the_apps_language(client, pseudo_catalogue):
    """What an app does with a coded error: English detail for an older app, and the catalogue text
    of its own language, filled with the error's params, for this one."""
    r = await client.get("/api/v1/templates/999999999")
    assert r.status_code == 404
    missing = r.json()
    assert missing == {"detail": "template not found", "code": "templates.template_not_found", "params": {}}

    r = await client.post("/api/v1/templates", json={"name": "A", "shortcut": "sig"})
    assert r.status_code == 201, r.text
    r = await client.post("/api/v1/templates", json={"name": "B", "shortcut": "sig"})
    assert r.status_code == 409
    taken = r.json()
    assert taken["detail"] == "another template already uses ;sig"
    assert (taken["code"], taken["params"]) == ("templates.shortcut_taken", {"shortcut": "sig"})

    r = await client.get("/api/v1/i18n/messages", params={"lang": "en-XA"})
    assert r.json()["lang"] == "en-XA"
    messages = r.json()["messages"]
    shown = {e["code"]: i18n.interpolate(messages[e["code"]], e["params"]) for e in (missing, taken)}
    assert shown["templates.template_not_found"] == pseudo("template not found") == "[ţéɱƥļáţé ñóţ ƒóúñð ••••••]"
    assert shown["templates.shortcut_taken"] == i18n.interpolate(
        pseudo("another template already uses ;{{shortcut}}"), {"shortcut": "sig"})
    assert shown["templates.shortcut_taken"].startswith("[áñóţĥéŕ ţéɱƥļáţé áļŕéáðý úšéš ;sig ")
    # Every coded error the routes raise has its text there.
    assert {"dictation.not_set_up", "calendar.no_invitation", "invites.used", "speakr.not_connected"} <= set(messages)


def test_the_android_case_file_matches_the_catalogue():
    """android/notes/src/test/resources/server-errors-en.json copies a few texts (the Android build
    sees only android/); a changed English text must be copied there too."""
    cases = json.loads((CODE.parent.parent / "android/notes/src/test/resources/server-errors-en.json")
                       .read_text(encoding="utf-8"))
    assert cases["messages"]
    for code, text in cases["messages"].items():
        assert i18n.t(f"errors.{code}") == text, code
    assert {c["code"] for c in cases["cases"]} == set(cases["messages"])
