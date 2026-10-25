"""The memory relevance eval: which notes reach Ambrose's prompt for a request.

The fixture (tests/fixtures/memory_relevance.yaml) holds a synthetic memory set and about forty
requests, each labelled with the notes that should be used (`use`) and the ones that may be (`ok`).
`seed` writes the memory set for a user; `run` asks `memory.context_block` for every request and
scores what it returned:

- precision: of the notes injected (pinned ones aside), the share that were wanted or allowed;
- recall: of the wanted notes, the share that were injected;
- notes per turn: injected notes (pinned aside) per request;
- empty turns kept empty: requests with nothing to use that got no note at all;
- relevance check time (p50/p95) when a decision model checked candidates.

tests/test_memory_eval.py runs it with the deterministic test embedder and holds the thresholds.
With MAILR_MEMORY_EVAL_EMBED_URL (and MAILR_MEMORY_EVAL_MICA_URL) set it runs against real
endpoints and prints the report.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Any

import yaml

FIXTURE = Path(__file__).parent / "fixtures" / "memory_relevance.yaml"


def load(path: Path = FIXTURE, *, held_out: bool | None = None) -> dict[str, Any]:
    """The fixture. `held_out`: True keeps only the requests written after calibration, False only
    the calibration set, None both."""
    import os

    fx = yaml.safe_load(path.read_text())
    if held_out is None and os.environ.get("MAILR_MEMORY_EVAL_SET"):
        held_out = os.environ["MAILR_MEMORY_EVAL_SET"] == "held_out"
    if held_out is not None:
        fx["requests"] = [r for r in fx["requests"] if bool(r.get("held_out")) == held_out]
    return fx


async def seed(db, p, fx: dict[str, Any]) -> dict[str, int]:
    """Write the fixture's entities and notes for p's user. Returns note key → memory id."""
    from mailr.knowledge import memory

    ents: dict[str, int] = {}
    for key, e in fx["entities"].items():
        if e.get("self"):
            row = await memory.self_entity(db, p, create=True)
            row.name = e["name"]
        else:
            row, _ = await memory.upsert_entity(db, p, {k: v for k, v in e.items() if k != "self"})
        ents[key] = row.id
    ids: dict[str, int] = {}
    for key, n in fx["notes"].items():
        data: dict[str, Any] = {"text": n["text"], "kind": n.get("kind", "fact"), "pinned": bool(n.get("pinned"))}
        if n.get("about"):
            data["entity_id"] = ents[n["about"]]
        m, _ = await memory.remember(db, p, data)
        ids[key] = m.id
    await db.flush()
    return ids


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, round(q * (len(xs) - 1))))
    return xs[i]


async def run(db, p, fx: dict[str, Any], ids: dict[str, int], *, verbose: bool = False) -> dict[str, Any]:
    from mailr.knowledge import memory

    key_of = {v: k for k, v in ids.items()}
    pinned = {ids[k] for k, n in fx["notes"].items() if n.get("pinned")}
    tp = fp = wanted = injected = 0
    empty = empty_kept = 0
    pinned_seen = 0
    check_ms: list[float] = []
    block_ms: list[float] = []
    rows: list[dict[str, Any]] = []
    checks: list[tuple[float, str, str, Any]] = []
    for r in fx["requests"]:
        use = {ids[k] for k in r.get("use") or []}
        ok = {ids[k] for k in r.get("ok") or []}
        t0 = time.monotonic()
        _, info = await memory.context_block(db, p, query=r["q"], emails=r.get("emails") or [], query_sensitive=False)
        block_ms.append((time.monotonic() - t0) * 1000)
        got = [i for i in info["memory_ids"] if i not in pinned]
        pinned_seen += len([i for i in info["memory_ids"] if i in pinned])
        rel = info.get("relevance") or {}
        if rel.get("checked"):
            check_ms.append(float(rel.get("ms") or 0))
        good = [i for i in got if i in use]
        bad = [i for i in got if i not in use and i not in ok]
        tp += len(good)
        fp += len(bad)
        wanted += len(use)
        injected += len(got)
        if not use:
            empty += 1
            empty_kept += 0 if got else 1
        for mid, pv in (rel.get("answers") or {}).items():
            checks.append((pv, "use" if mid in use else "ok" if mid in ok else "wrong", r["q"], key_of.get(mid)))
        rows.append({"q": r["q"], "got": [key_of.get(i, i) for i in got],
                     "missed": [key_of[i] for i in use if i not in got],
                     "wrong": [key_of.get(i, i) for i in bad]})
    n = len(fx["requests"])
    out = {
        "requests": n,
        "precision": round(tp / (tp + fp), 3) if tp + fp else 1.0,
        "recall": round(tp / wanted, 3) if wanted else 1.0,
        "notes_per_turn": round(injected / n, 2),
        "wrong_notes": fp,
        "empty_kept_empty": f"{empty_kept}/{empty}",
        "pinned_per_turn": round(pinned_seen / n, 2),
        "block_ms_p50": round(statistics.median(block_ms), 1) if block_ms else 0.0,
        "block_ms_p95": round(_pct(block_ms, 0.95), 1),
        "check_ms_p50": round(statistics.median(check_ms), 1) if check_ms else None,
        "check_ms_p95": round(_pct(check_ms, 0.95), 1) if check_ms else None,
        "rows": rows,
        "checks": sorted(checks),
    }
    return out


def report(out: dict[str, Any], title: str = "memory relevance eval") -> str:
    lines = [f"== {title}"]
    lines += [f"{k}: {v}" for k, v in out.items() if k not in ("rows", "checks")]
    for r in out["rows"]:
        if r["missed"] or r["wrong"]:
            lines.append(f"  {r['q']!r}: got={r['got']} missed={r['missed']} wrong={r['wrong']}")
    return "\n".join(lines)


async def calibrate(db, p, fx: dict[str, Any], ids: dict[str, int]) -> list[dict[str, Any]]:
    """The vector channel alone, per cosine-distance threshold: of the (request, note) pairs under
    it, how many were wanted (`use`), allowed (`ok`) or wrong. Picks the per-model threshold."""
    from sqlalchemy import select

    from mailr.intel import embed as emb
    from mailr.models import Memory

    pairs: list[tuple[float, str]] = []
    for r in fx["requests"]:
        qv = await emb.embed_query(db, r["q"], sensitive=False, user_id=p.user_id)
        if qv is None:
            return []
        vec, key = qv
        dist = Memory.embedding.cosine_distance(vec)
        rows = (await db.execute(select(Memory.id, dist.label("d")).where(
            Memory.user_id == p.user_id, Memory.embed_model == key))).all()
        use = {ids[k] for k in r.get("use") or []}
        ok = {ids[k] for k in r.get("ok") or []}
        for row in rows:
            pairs.append((float(row.d), "use" if row.id in use else "ok" if row.id in ok else "wrong"))
    total_use = sum(1 for _, c in pairs if c == "use")
    out = []
    for t in [x / 100 for x in range(30, 75, 2)]:
        under = [c for d, c in pairs if d < t]
        u, w = under.count("use"), under.count("wrong")
        out.append({"max_distance": t, "use": u, "ok": under.count("ok"), "wrong": w,
                    "precision": round(u / (u + w), 3) if u + w else 1.0, "recall": round(u / total_use, 3)})
    return out
