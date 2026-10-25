"""Literal-False lint (outbound data policy, part 4).

1. Every call in mailr/ that passes a literal `sensitive=False` or `query_sensitive=False` is
   listed in tests/outbound_allowlist.py with a reason, and every listed entry still exists (an
   entry whose call was removed is deleted, so the list stays the list of real exceptions).
2. The query's data class is a required keyword with no default in the functions that embed a
   search or recall query: intel/embed.embed_query (`sensitive`), intel/search.passages and
   .conversations, knowledge/memory.recall, .recall_hits, .prepare_context and .context_block
   (`query_sensitive`). A caller cannot leave it out and get "not sensitive" by accident.
"""

import ast
import inspect
from collections import Counter
from pathlib import Path

from outbound_allowlist import ALLOWED

ROOT = Path(__file__).resolve().parent.parent / "mailr"
KEYWORDS = ("sensitive", "query_sensitive")


def literal_false_calls() -> Counter:
    found: Counter = Counter()

    def visit(node: ast.AST, scope: list[str], rel: str) -> None:
        for c in ast.iter_child_nodes(node):
            s = scope
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                s = [*scope, c.name]
            if isinstance(c, ast.Call):
                for k in c.keywords:
                    if k.arg in KEYWORDS and isinstance(k.value, ast.Constant) and k.value.value is False:
                        found[f"{rel}::{'.'.join(s) or '<module>'}::{ast.unparse(c.func)}::{k.arg}"] += 1
            visit(c, s, rel)

    for f in sorted(ROOT.rglob("*.py")):
        visit(ast.parse(f.read_text(encoding="utf-8")), [], f.relative_to(ROOT).as_posix())
    return found


def test_every_literal_false_is_listed_with_a_reason():
    found = literal_false_calls()
    missing = {k: n for k, n in found.items() if k not in ALLOWED}
    assert not missing, ("literal sensitive=False / query_sensitive=False without an entry in "
                         f"tests/outbound_allowlist.py (add one with a reason, or pass the real class): {missing}")
    wrong = {k: (n, ALLOWED[k][0]) for k, n in found.items() if ALLOWED[k][0] != n}
    assert not wrong, f"the number of such calls changed (found, listed): {wrong}"
    stale = sorted(set(ALLOWED) - set(found))
    assert not stale, f"entries in tests/outbound_allowlist.py whose call is gone (delete them): {stale}"
    assert all(isinstance(r, str) and len(r) > 20 for _, r in ALLOWED.values())


def test_the_skill_try_it_box_declares_its_class():
    """The audit's one unjustified literal (knowledge/routes.py test_skill) is gone."""
    assert not any(k.startswith("knowledge/routes.py::test_skill::") for k in literal_false_calls())


def test_query_class_is_a_required_keyword():
    from mailr.intel import embed, search
    from mailr.knowledge import memory

    funcs = [(embed.embed_query, "sensitive"), (search.passages, "query_sensitive"),
             (search.conversations, "query_sensitive"), (memory.recall, "query_sensitive"),
             (memory.recall_hits, "query_sensitive"), (memory.prepare_context, "query_sensitive"),
             (memory.context_block, "query_sensitive")]
    for fn, name in funcs:
        prm = inspect.signature(fn).parameters[name]
        assert prm.kind is inspect.Parameter.KEYWORD_ONLY, (fn.__qualname__, name)
        assert prm.default is inspect.Parameter.empty, f"{fn.__module__}.{fn.__qualname__}: {name} has a default"
