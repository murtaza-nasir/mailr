"""The three-way merge of live editing (core/merge3.py).

The cases in fixtures/merge3_cases.json are shared with the web app (frontend/src/lib/live/merge3.test.ts)
and Android (LiveMergeTest.kt): all three implementations must give these results."""

import json
from pathlib import Path

import pytest

from mailr.core import merge3

CASES = json.loads((Path(__file__).parent / "fixtures" / "merge3_cases.json").read_text())


@pytest.mark.parametrize("case", CASES["text"], ids=[c["name"] for c in CASES["text"]])
def test_text_cases(case):
    r = merge3.merge_text(case["base"], case["mine"], case["theirs"], case["mode"])
    assert r.ok is case["ok"]
    assert r.text == case["result"]
    assert [c.out() for c in r.conflicts] == case["conflicts"]


@pytest.mark.parametrize("case", CASES["fields"], ids=[c["name"] for c in CASES["fields"]])
def test_field_cases(case):
    r = merge3.merge_fields(case["base"], case["mine"], case["theirs"], case["text_fields"])
    assert r.ok is case["ok"]
    assert r.merged == case["merged"]
    assert r.conflicts == case["conflicts"]


def _long(case, side=None):
    lines = [case["line"].format(n=n) for n in range(1, case["lines"] + 1)]
    for s in ([side] if side else ["mine", "theirs"]):
        n, text = case[s]
        lines[n - 1] = text
    return "".join(lines)


@pytest.mark.parametrize("case", CASES["long"], ids=[c["name"] for c in CASES["long"]])
def test_long_text(case, monkeypatch):
    # The quadratic table (`_dp`) is only for short stretches between unique anchors; filling it
    # for the whole text would be lines x lines cells (9 million for 3,000 lines).
    cells = []
    real_dp = merge3._dp

    def counted(a, a0, a1, b, b0, b1, out):
        cells.append((a1 - a0) * (b1 - b0))
        return real_dp(a, a0, a1, b, b0, b1, out)

    monkeypatch.setattr(merge3, "_dp", counted)
    base = "".join(case["line"].format(n=n) for n in range(1, case["lines"] + 1))
    r = merge3.merge_text(base, _long(case, "mine"), _long(case, "theirs"), "lines")
    assert sum(cells) <= case["lines"], cells
    assert r.ok is case["ok"]
    if case["ok"]:
        assert r.text == _long(case)
    else:
        assert len(r.conflicts) == 1 and r.text == _long(case, "mine")


def test_tokens_give_the_text_back():
    for text, mode in [("a\nb\n\nc", "lines"), ("<p>a</p><div>b<br>c</div>tail", "html"), ("", "lines"),
                       ("<P>Up</P><BR/>x", "html")]:
        assert "".join(merge3.tokens(text, mode)) == text


def test_html_key_normalises_spelling():
    assert merge3.key('<div class="a" dir="ltr">Hi&nbsp; there<br/></div>', "html") == "<p>Hi there<br></p>"


def test_repeated_lines_still_merge():
    base = "x\n" * 50 + "end\n"
    mine = "start\n" + base
    theirs = base.replace("end\n", "the end\n")
    r = merge3.merge_text(base, mine, theirs)
    assert r.ok and r.text == "start\n" + "x\n" * 50 + "the end\n"


def test_same_treats_empty_as_missing():
    assert merge3.same(None, "") and merge3.same([], None) and not merge3.same("a", None)
