"""Table views (tables/views.py) against spec/cases/table-view-cases.yaml.

The phone evaluates a view from its own rows when it is offline (notes-core ViewEval), and its unit
test runs the same vectors from a copy in the Android test resources: this test also checks the copy
is the same file."""

import json
from datetime import date
from pathlib import Path

import pytest

from mailr.tables import views as vw
from mailr.tables.types import Col, Ctx

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "spec" / "cases" / "table-view-cases.yaml"
ANDROID_COPY = ROOT / "android" / "app" / "src" / "test" / "resources" / "interaction" / "table-view-cases.yaml"


def _load() -> tuple[dict, list[dict]]:
    top: dict = {}
    cases: list[dict] = []
    in_cases = False
    for line in CASES.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if line == "cases:":
            in_cases = True
        elif in_cases and line.startswith("- "):
            cases.append(json.loads(line[2:]))
        else:
            k, v = line.split(":", 1)
            top[k.strip()] = json.loads(v)
            in_cases = False
    return top, cases


TOP, CASES_ = _load()
COLS = {c["id"]: Col(c["id"], c["name"], c["type"], c.get("config") or {}) for c in TOP["columns"]}
CTX = Ctx(today=date.fromisoformat(TOP["today"]), tz=TOP["tz"])


class _Row:
    def __init__(self, d: dict):
        self.id, self.order, self.cells = d["id"], d["order"], d["cells"]


ROWS = [_Row(r) for r in TOP["rows"]]


def test_the_android_copy_is_the_same_file():
    assert ANDROID_COPY.read_text() == CASES.read_text()


def test_case_ids_are_unique():
    assert len(CASES_) >= 60
    assert len({c["id"] for c in CASES_}) == len(CASES_)


@pytest.mark.parametrize("case", CASES_, ids=[c["id"] for c in CASES_])
def test_case(case):
    clean = vw.clean_spec(case["spec"], COLS, TOP["primary"])
    rows = vw.apply_filters(ROWS, clean["filters"], COLS, CTX)
    assert [r.id for r in vw.sort_rows(rows, clean["sort"], COLS, TOP["titles"])] == case["expect"]
