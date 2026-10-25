"""The fake network and the coverage meter of tests/test_canary_sweep.py.

`FakeNet` answers every HTTP request to a registered host in this process (httpx.AsyncClient.send
and httpx.Client.send are wrapped, so any client, transport or pinning is covered) and records the
request: host, URL, body. Hosts resolve through a fake resolver: a hosted host to a public
address, a local one to a private address. Nothing reaches real DNS or the network.

Kinds of fake service:
- "openai": an OpenAI-compatible model server: GET /models, POST /chat/completions (streamed or
  not; scripted turns for the assistant loop; otherwise a minimal answer that fits the request's
  JSON schema) and POST /embeddings (embedfake vectors).
- "jev": the Jev decision protocol (POST /systemone) with empty answers.
- "search": a SearXNG-style search API (GET /search?q=…&format=json).
- "reader": a page reader (Jina-style JSON) answering every URL with a short page.
- "page": a web page (HTML), for a direct page fetch.

`Meter` wraps the model entry points (llm.chat, llm.chat_stream, llm.embed_request,
embed.embed_texts, embed.embed_query, embed.embed_pending_passages, jev.decide, jev.smart,
jev.category) and records, for every call, its `feature` and the calling site
("<path under mailr/>::<function>"), so the sweep can compare what it ran with every call site and
feature name in the code (`static_sites`, `static_features`).
"""

from __future__ import annotations

import ast
import json
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import embedfake
import httpx

MAILR = Path(__file__).resolve().parents[2] / "mailr"
PUBLIC_IP = "93.184.216.34"
PRIVATE_IP = "10.77.0.5"


@dataclass
class Rec:
    host: str
    kind: str
    locality: str
    method: str
    url: str
    body: str
    where: str = ""  # the innermost mailr function outside the model plumbing that made the request

    def text(self) -> str:
        return (self.url + "\n" + self.body).lower()


def _where() -> str:
    """The innermost frame in mailr/ outside the model plumbing: who made this request."""
    f = sys._getframe(1)
    while f is not None:
        rel = _rel(f.f_code.co_filename)
        if rel is not None and rel not in INTERNAL:
            return f"{rel}::{f.f_code.co_qualname.replace('.<locals>', '')}"
        f = f.f_back
    return "?"


def sample(schema: Any, depth: int = 0) -> Any:
    """A minimal value that fits a JSON schema (enough for a parser to accept it)."""
    if not isinstance(schema, dict) or depth > 8:
        return None
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    for key in ("anyOf", "oneOf"):
        if schema.get(key):
            opts = [o for o in schema[key] if isinstance(o, dict) and o.get("type") != "null"] or schema[key]
            return sample(opts[0], depth + 1)
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "null")
    if t == "object" or "properties" in schema:
        props = schema.get("properties") or {}
        return {k: sample(v, depth + 1) for k, v in props.items()}
    if t == "array":
        return []
    if t == "string":
        return "ok"
    if t == "integer":
        return 0
    if t == "number":
        return 0.0
    if t == "boolean":
        return False
    return None


def _sse(chunks: list[dict[str, Any]]) -> bytes:
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


def text_turn(t: str) -> list[dict[str, Any]]:
    return [{"choices": [{"delta": {"content": t}}]}, {"choices": [{"delta": {}, "finish_reason": "stop"}]}]


def tool_turn(name: str, args: dict[str, Any], call_id: str = "c1") -> list[dict[str, Any]]:
    return [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": call_id, "function": {
        "name": name, "arguments": json.dumps(args)}}]}, "finish_reason": "tool_calls"}]}]


@dataclass
class FakeNet:
    hosts: dict[str, tuple[str, str]] = field(default_factory=dict)  # host -> (kind, locality)
    records: list[Rec] = field(default_factory=list)
    script: list[list[dict[str, Any]]] = field(default_factory=list)  # streamed chat turns, in order

    def add(self, host: str, kind: str, locality: str) -> str:
        self.hosts[host] = (kind, locality)
        return host

    def install(self, monkeypatch) -> FakeNet:
        net = self
        real_async = httpx.AsyncClient.send
        real_sync = httpx.Client.send

        async def asend(client, request, *a, **kw):
            if request.url.host not in net.hosts:
                return await real_async(client, request, *a, **kw)
            body = await request.aread()
            return net.answer(request, body)

        def ssend(client, request, *a, **kw):
            if request.url.host not in net.hosts:
                return real_sync(client, request, *a, **kw)
            return net.answer(request, request.read())

        monkeypatch.setattr(httpx.AsyncClient, "send", asend)
        monkeypatch.setattr(httpx.Client, "send", ssend)
        real_gai = socket.getaddrinfo

        def gai(host, port=None, *a, **kw):
            h = host.decode() if isinstance(host, bytes) else (host or "")
            if h in net.hosts:
                ip = PRIVATE_IP if net.hosts[h][1] == "local" else PUBLIC_IP
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 443))]
            return real_gai(host, port, *a, **kw)

        monkeypatch.setattr(socket, "getaddrinfo", gai)
        return self

    # -------------------------------------------------------------- answers

    def answer(self, request: httpx.Request, body: bytes) -> httpx.Response:
        host = request.url.host
        kind, locality = self.hosts[host]
        text = body.decode("utf-8", errors="replace")
        self.records.append(Rec(host, kind, locality, request.method, str(request.url), text, _where()))
        path = request.url.path
        if kind == "openai":
            return self._openai(request, path, text)
        if kind == "jev":
            return httpx.Response(200, json={"answers": {}}, request=request)
        if kind == "search":
            q = request.url.params.get("q") or ""
            return httpx.Response(200, json={"results": [
                {"url": "https://page.sweep.test/result", "title": f"Result for {q[:40]}", "content": "A result."}]},
                request=request)
        if kind == "page":
            return httpx.Response(200, content=b"<html><head><title>Page</title></head><body><p>"
                                  + b"A page about the weather. " * 20 + b"</p></body></html>",
                                  headers={"content-type": "text/html; charset=utf-8"}, request=request)
        if kind == "reader":
            return httpx.Response(200, json={"data": {"title": "Page", "content": "Page text.", "url": str(request.url)},
                                             "title": "Page", "content": "Page text."}, request=request)
        return httpx.Response(404, request=request)

    def _openai(self, request: httpx.Request, path: str, text: str) -> httpx.Response:
        if path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m"}, {"id": "e"}]}, request=request)
        try:
            body = json.loads(text) if text else {}
        except ValueError:
            body = {}
        if path.endswith("/embeddings"):
            inputs = body.get("input") or []
            inputs = [inputs] if isinstance(inputs, str) else inputs
            data = [{"index": i, "embedding": embedfake.vector(str(t))} for i, t in enumerate(inputs)]
            return httpx.Response(200, json={"data": data}, request=request)
        if path.endswith("/chat/completions"):
            if body.get("stream"):
                turn = self.script.pop(0) if self.script else text_turn("Done.")
                return httpx.Response(200, content=_sse(turn), headers={"content-type": "text/event-stream"},
                                      request=request)
            rf = body.get("response_format") or {}
            schema = ((rf.get("json_schema") or {}).get("schema")) if isinstance(rf, dict) else None
            content = json.dumps(sample(schema)) if schema else ("{}" if rf else "Done.")
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content},
                                                          "finish_reason": "stop"}]}, request=request)
        return httpx.Response(404, request=request)

    # -------------------------------------------------------------- checks

    def leaks(self, canaries: list[str], *, locality: str = "hosted") -> list[tuple[str, Rec]]:
        """(canary, request) for every canary found in a request to a service of `locality`."""
        out = []
        for r in self.records:
            if r.locality != locality:
                continue
            t = r.text()
            out += [(c, r) for c in canaries if c.lower() in t]
        return out


# ------------------------------------------------------------------ coverage meter

# (module, attribute, the kind of call): the entry points every model call passes.
ENTRY = [("mailr.intel.llm", "chat"), ("mailr.intel.llm", "chat_stream"), ("mailr.intel.llm", "embed_request"),
         ("mailr.intel.embed", "embed_texts"), ("mailr.intel.embed", "embed_query"),
         ("mailr.intel.embed", "embed_pending_passages"), ("mailr.intel.jev", "decide"),
         ("mailr.intel.jev", "smart"), ("mailr.intel.jev", "category")]
# Files whose calls of these entry points are plumbing between them, not features.
INTERNAL = {"intel/llm.py", "intel/embed.py", "intel/jev.py"}
ALIASES = {"llm", "emb", "embed", "jev"}


def _rel(filename: str) -> str | None:
    try:
        return Path(filename).resolve().relative_to(MAILR).as_posix()
    except ValueError:
        return None


@dataclass
class Meter:
    sites: set[str] = field(default_factory=set)
    features: set[str] = field(default_factory=set)
    calls: list[tuple[str, str, str]] = field(default_factory=list)  # (site, entry, feature)

    def install(self, monkeypatch) -> Meter:
        import importlib
        import inspect

        meter = self
        for modname, attr in ENTRY:
            mod = importlib.import_module(modname)
            real = getattr(mod, attr)

            def site_of(frame) -> str | None:
                f = frame
                while f is not None:
                    rel = _rel(f.f_code.co_filename)
                    if rel is not None and rel not in INTERNAL:
                        return f"{rel}::{f.f_code.co_qualname.replace('.<locals>', '')}"
                    if rel is None and "helpers/outbound_sweep.py" not in f.f_code.co_filename:
                        return None
                    f = f.f_back
                return None

            def note(entry: str, kw: dict[str, Any], frame) -> None:
                feat = kw.get("feature") or ("search" if "embed" in entry else "decision" if "jev" in entry
                                             else kw.get("task") or "chat")
                caller = _rel(frame.f_code.co_filename) if frame is not None else None
                if caller in INTERNAL:
                    meter.features.add(feat)
                    return
                site = site_of(frame)
                meter.features.add(feat)
                if site:
                    meter.sites.add(site)
                    meter.calls.append((site, entry, feat))

            entry = f"{modname.rsplit('.', 1)[-1]}.{attr}"
            if inspect.isasyncgenfunction(real):
                def wrap(real=real, entry=entry):
                    def gen(*a, **kw):  # the generator's creator is the call site, not who iterates it
                        note(entry, kw, sys._getframe(1))
                        return real(*a, **kw)
                    return gen
            else:
                def wrap(real=real, entry=entry):
                    async def fn(*a, **kw):
                        note(entry, kw, sys._getframe(1))
                        return await real(*a, **kw)
                    return fn
            monkeypatch.setattr(mod, attr, wrap())
        return self


def static_sites() -> dict[str, str]:
    """Every call of a model entry point in mailr/ outside the plumbing files:
    "<path>::<function>" -> the entry called."""
    names = {attr for _, attr in ENTRY}
    out: dict[str, str] = {}
    for f in sorted(MAILR.rglob("*.py")):
        rel = f.relative_to(MAILR).as_posix()
        if rel in INTERNAL:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))

        def visit(node: ast.AST, scope: list[str], rel: str = rel) -> None:
            for c in ast.iter_child_nodes(node):
                s = [*scope, c.name] if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else scope
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr in names \
                        and isinstance(c.func.value, ast.Name) and c.func.value.id in ALIASES:
                    out[f"{rel}::{'.'.join(s)}"] = f"{c.func.value.id}.{c.func.attr}"
                elif isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "embed_query":
                    out[f"{rel}::{'.'.join(s)}"] = "embed_query"
                visit(c, s)

        visit(tree, [])
    return out


def static_features() -> set[str]:
    """Every literal feature= name given to a model call in mailr/."""
    out: set[str] = set()
    for f in sorted(MAILR.rglob("*.py")):
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call):
                for k in n.keywords:
                    if k.arg == "feature" and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str):
                        out.add(k.value.value)
    return out


def host_of(url: str) -> str:
    return urlsplit(url).hostname or ""
