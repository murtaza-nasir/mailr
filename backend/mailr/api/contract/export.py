"""The OpenAPI document of the typed routes only (backend/README.md, "Typed API contract").

    python -m mailr.api.contract.export                 print it
    python -m mailr.api.contract.export --write PATH    write it (tools/api-types does)
    python -m mailr.api.contract.export --check PATH    exit 1 when PATH is not what it would write

The full document (/api/v1/openapi.json, development only) lists every route, most of them with
no response schema. This one holds the operations that declare a response model and the schemas
they use, so the generated clients (frontend/src/api/schema.gen.ts) carry only checked types.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def is_typed(op: dict[str, Any]) -> bool:
    """An operation whose success answer has a schema: its route declares a response model (routes
    without one get an empty schema)."""
    for code, resp in (op.get("responses") or {}).items():
        if code.startswith("2"):
            schema = (resp.get("content") or {}).get("application/json", {}).get("schema")
            if schema:
                return True
    return False


def _refs(obj: Any, found: set[str]) -> None:
    if isinstance(obj, dict):
        ref = obj.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.add(ref.rsplit("/", 1)[1])
        for v in obj.values():
            _refs(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _refs(v, found)


def document() -> dict[str, Any]:
    from mailr.main import create_app

    app = create_app()
    full = app.openapi()
    paths: dict[str, Any] = {}
    for path, methods in sorted(full["paths"].items()):
        kept = {m: op for m, op in methods.items() if isinstance(op, dict) and is_typed(op)}
        if kept:
            paths[path] = kept
    schemas = full.get("components", {}).get("schemas", {})
    need: set[str] = set()
    _refs(paths, need)
    todo = list(need)
    while todo:
        name = todo.pop()
        more: set[str] = set()
        _refs(schemas.get(name, {}), more)
        for n in more - need:
            need.add(n)
            todo.append(n)
    # A fixed version: the file is checked into the repository, and the server's version (the full
    # document's info.version) changes with every release.
    return {"openapi": full["openapi"], "info": {"title": "mailr (typed routes)", "version": "0.1.0"},
            "paths": paths, "components": {"schemas": {n: schemas[n] for n in sorted(need)}}}


def render() -> str:
    return json.dumps(document(), indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    import base64
    import os

    # The app wants a key to build (CI has none at this step); no request runs, so any key will do.
    os.environ.setdefault("MAILR_SECRET_KEY", base64.b64encode(os.urandom(32)).decode())
    text = render()
    if len(argv) == 2 and argv[0] == "--write":
        with open(argv[1], "w", encoding="utf-8") as f:
            f.write(text)
        return 0
    if len(argv) == 2 and argv[0] == "--check":
        try:
            with open(argv[1], encoding="utf-8") as f:
                current = f.read()
        except FileNotFoundError:
            current = ""
        if current != text:
            print(f"{argv[1]} is out of date: run tools/api-types", file=sys.stderr)
            return 1
        return 0
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
