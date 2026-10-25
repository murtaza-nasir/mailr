# ACT-SURFACES: api
"""The checklist item grammar against spec/cases/notes-quickadd-cases.yaml.

The web twin (frontend/src/lib/itemadd.test.ts) runs the same vectors. The server parses the lines
of note.add_items, checkitem.add and note.to_checklist with this grammar."""

import json
from pathlib import Path

import pytest

from mailr.notes import itemadd

CASES = Path(__file__).resolve().parents[2] / "spec" / "cases" / "notes-quickadd-cases.yaml"


def _load() -> tuple[dict, list[dict]]:
    top: dict = {}
    cases: list[dict] = []
    for line in CASES.read_text().splitlines():
        if line.startswith("  - "):
            cases.append(json.loads(line[4:]))
        elif line and not line.startswith(("#", " ")) and ":" in line and not line.startswith("cases:"):
            k, v = line.split(":", 1)
            top[k.strip()] = json.loads(v)
    return top, cases


TOP, CASES_ = _load()
LISTS = TOP["lists"]


def _run(case: dict) -> itemadd.Parse:
    opts = case.get("opts") or {}
    if case["mode"] == "capture":
        return itemadd.parse(case["in"], "capture", lists=LISTS, default_list=TOP["defaultList"],
                             keep=opts.get("keep"))
    lst = next(x for x in LISTS if x["id"] == case["list"])
    return itemadd.parse(case["in"], case["mode"], sections=lst["sections"], keep=opts.get("keep"),
                         prefix_creates=bool(opts.get("prefixCreates")))


@pytest.mark.parametrize("case", CASES_, ids=lambda c: c["id"])
def test_item_grammar_vectors(case):
    exp = case["expect"]
    if case["mode"] == "key":
        assert itemadd.item_key(case["in"]) == exp["key"]
        return
    if case["mode"] == "quick":
        q = itemadd.quick(case["in"], LISTS)
        assert q["kind"] == exp["kind"]
        if q["kind"] == "note":
            assert {"title": q["title"], "body": q["body"]} == exp["note"]
            return
        assert q["list"] == exp["list"]
        items = [{"text": i["text"], "qty": i["qty"] and {"amount": i["qty"]["amount"], "unit": i["qty"]["unit"]},
                  "section": i["section"], "checked": i["checked"]} for i in q["items"]]
        want = [{"text": i["text"], "qty": i.get("qty"), "section": i.get("section"),
                 "checked": i.get("checked", False)} for i in exp["items"]]
        assert items == want
        assert [f"{t['kind']}:{t['text']}" for t in q["tokens"]] == exp.get("tokens", [])
        return
    got = _run(case)
    items = [{"text": i.text, "qty": i.qty and {"amount": i.qty["amount"], "unit": i.qty["unit"]},
              "section": i.section, "checked": i.checked} for i in got.items]
    want = [{"text": i["text"], "qty": i.get("qty"), "section": i.get("section"), "checked": i.get("checked", False)}
            for i in exp["items"]]
    assert items == want
    assert got.new_sections == exp.get("new_sections", [])
    assert [f"{t.kind}:{t.text}" for t in got.tokens] == exp.get("tokens", [])
    if case["mode"] == "capture":
        assert got.list == exp["list"]


def _u16(s: str) -> list[int]:
    """Code point index → UTF-16 offset."""
    out, n = [], 0
    for ch in s:
        out.append(n)
        n += 2 if ord(ch) > 0xFFFF else 1
    out.append(n)
    return out


@pytest.mark.parametrize("text", TOP["typing"])
@pytest.mark.parametrize("mode", ["list", "capture"])
def test_item_grammar_typing_prefixes(text, mode):
    """Every prefix parses, and each token's text sits at its offsets."""
    for n in range(1, len(text) + 1):
        prefix = text[:n]
        if mode == "capture":
            res = itemadd.parse(prefix, "capture", lists=LISTS, default_list=TOP["defaultList"])
        else:
            res = itemadd.parse(prefix, "list", sections=LISTS[0]["sections"])
        units = prefix.encode("utf-16-le")
        for t in res.tokens:
            assert units[t.start * 2:t.end * 2].decode("utf-16-le") == t.text, (prefix, t)


def test_item_key_matches_duplicates():
    assert itemadd.item_key("Eggs") == itemadd.item_key("egg") == itemadd.item_key("EGGS")
    assert itemadd.item_key("tomatoes") == itemadd.item_key("Tomato")


def test_export_line_parses_back():
    """The Markdown export writes `milk ×2` and `500 g flour`; both parse back to the same item."""
    for text, qty in (("milk", {"amount": "2", "unit": None}), ("flour", {"amount": "500", "unit": "g"}),
                      ("rice", {"amount": "1.5", "unit": "kg"})):
        line = itemadd.line_for(text, qty)
        got = itemadd.parse(line, "list").items[0]
        assert got.text == text and {"amount": got.qty["amount"], "unit": got.qty["unit"]} == qty
