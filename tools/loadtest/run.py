#!/usr/bin/env python3
"""Load test: many users on one server, with one or more workers (internal; not part of the gate).

    backend/.venv/bin/python tools/loadtest/run.py --users 10,50,100 --workers 1,2 [--legacy]

Run it under tools/agent-env (`tools/agent-env run …`), so the databases are this worktree's.

For each user count it builds a database, a copy of mailr_template, with fictional mailboxes
(tests/scale_fixture.py, --conversations each, two messages per conversation) and one more user
whose import of --backfill messages waits for triage. Then for each worker count it copies that
database and starts the stub model server (tools/loadtest/stub_model.py: fixed latency, a few
requests at a time like one GPU), the API (`mailr serve`) and the workers
(tools/loadtest/worker.py), and for --duration seconds:

- every user but the importer gets new mail, one message every --mail-every seconds on average
  (a message row and its triage job, as ingest writes them), and sends one every --send-every
  seconds (a send job; the sending itself is a fixed 50 ms);
- every user opens the inbox every --ui-every seconds on average, and every third time searches;
- the importer gets a burst of --burst new messages at the start, besides the backfill.

Measured: new mail → triaged (the triage job finished), p50/p95, for the other users and for the
importer's burst; a send's due time → done; inbox page and search times through the API; the
backfill's messages triaged per minute; the stub's model calls. --legacy runs the workers with the
claims as they were before per-user turns (each lane's earliest due job) and no cap on model calls.
The databases are dropped at the end. One JSON line per run goes to stdout, then a table.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
HERE = Path(__file__).resolve().parent
PASSWORD = "load-password-123"
SOURCES = 40  # inbox conversations per user that new mail copies


def _env(db_url: str, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env["MAILR_DATABASE_URL"] = db_url
    env["PYTHONPATH"] = str(BACKEND) + os.pathsep + env.get("PYTHONPATH", "")
    env.update({k: str(v) for k, v in extra.items()})
    return env


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, round(p / 100 * (len(xs) - 1))))
    return xs[k]


# ---------------- seed (a subprocess with the seed database's MAILR_DATABASE_URL) ----------------

async def seed(a) -> None:
    sys.path.insert(0, str(BACKEND))
    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import insert

    from mailr.core.access import FEATURES
    from mailr.core.auth import create_session, hash_password
    from mailr.core.crypto import new_wrapped_data_key
    from mailr.db import session_scope, sessionmaker
    from mailr.models import AccessPolicy, ModelEndpoint, User
    from tests import scale_fixture as sf

    out = []
    pw = hash_password(PASSWORD)
    async with session_scope() as db:
        await db.execute(text("CREATE TABLE IF NOT EXISTS loadtest_events (kind text, user_id int, t0 float8, "
                              "t1 float8, worker int)"))
        await db.execute(insert(AccessPolicy).values(
            group_name="loadtest", description="load test: everything",
            models={t: {"any": True, "allowed": [], "default": None} for t in ("chat", "classify")},
            features={k: True for k in FEATURES}, needs={}).on_conflict_do_nothing(index_elements=["group_name"]))
        db.add(ModelEndpoint(name="loadtest-stub", base_url=a.model_url, locality="local", adapter="openai",
                             models=["stub", "stub-embed"],
                             defaults={"chat": "stub", "classify": "stub", "embed": "stub-embed"}))
    t0 = time.perf_counter()
    for i in range(a.users + 1):
        importer = i == a.users
        async with sessionmaker()() as db:
            # A placeholder mail store account: the app's start provisions none for it (the load
            # test never touches the mail store).
            u = User(email=f"load{i}@load.example", name=f"Load {i}", role="user", password_hash=pw,
                     settings={}, email_verified=True, assigned_groups=["loadtest"],
                     stalwart_account_id=f"loadtest-{i}", stalwart_login=f"load{i}@load.example")
            db.add(u)
            await db.flush()
            u.data_key_wrapped = new_wrapped_data_key(u.id)
            await db.commit()
            sf.SCALE_CONVERSATIONS = a.backfill // 2 if importer else a.conversations
            built = await sf.build(db, u.id)
            if importer:
                await db.execute(text("UPDATE messages SET intel_state = 'backfill' WHERE user_id = :u"), {"u": u.id})
            raw, s = await create_session(db, u)
            src = list((await db.execute(text(
                "SELECT id FROM conversations WHERE user_id = :u AND label_ids @> ARRAY[:i]::int[] "
                "ORDER BY id LIMIT :n"), {"u": u.id, "i": built.labels["inbox"],
                                          "n": 5000 if importer else SOURCES})).scalars())
            await db.commit()
            out.append({"id": u.id, "token": raw, "csrf": s.csrf, "importer": importer, "sources": src})
        if i % 10 == 0:
            print(f"seed: {i + 1} users, {time.perf_counter() - t0:.0f}s", file=sys.stderr, flush=True)
    Path(a.out).write_text(json.dumps(out))


# ---------------- drive (a subprocess with the run database's MAILR_DATABASE_URL) ----------------

COPY_CONV = """INSERT INTO conversations (user_id, subject, norm_subject, snippet, last_date, first_date,
    message_count, unread_count, draft_count, has_attachments, starred, important, importance_score, category,
    label_ids, participants, muted, pinned, updated_at)
  SELECT user_id, subject, norm_subject, snippet, now(), now(), 1, 1, 0, false, false, false, 0, NULL,
    label_ids, participants, false, false, now() FROM conversations WHERE id = :src RETURNING id"""
COPY_MSG = """INSERT INTO messages (user_id, conversation_id, jmap_id, jmap_thread_id, blob_id, upstream,
    message_id_hdr, "references", subject, norm_subject, from_email, from_name, "to", cc, bcc, reply_to,
    delivered_to, date, received_at, size, snippet, body_text, has_attachments, attachments, auth_results,
    seen, flagged, answered, draft, important, label_ids, deleted, category, importance_score, sent_by_me,
    local_only, intel_state, created_at, updated_at, tsv, list_id, list_unsubscribe, unsub_method)
  SELECT user_id, :c, 'n' || :cs, 'nt' || :cs, NULL, '{}'::jsonb, '<n' || :cs || '@load.test>', "references",
    subject, norm_subject, from_email, from_name, "to", cc, bcc, reply_to, delivered_to, now(), now(), size,
    snippet, body_text, false, '[]'::jsonb, auth_results, false, false, false, false, false, label_ids, false,
    NULL, 0, false, false, 'pending', now(), now(), tsv, NULL, NULL, NULL
  FROM messages WHERE conversation_id = :src AND NOT sent_by_me ORDER BY id LIMIT 1 RETURNING id"""


async def new_mail(db, user: dict, t0: float | None = None) -> None:
    from sqlalchemy import text

    from mailr.core import jobs

    src = random.choice(user["sources"])
    c = (await db.execute(text(COPY_CONV), {"src": src})).scalar_one()
    m = (await db.execute(text(COPY_MSG), {"c": c, "cs": str(c), "src": src})).scalar()
    if m is None:
        return
    await jobs.enqueue(db, "intel.message", {"message_id": m, "t0": t0 or time.time()}, user_id=user["id"],
                       dedupe_key=f"intel.message:{m}")


async def drive(a) -> None:
    sys.path.insert(0, str(BACKEND))
    import httpx
    from sqlalchemy import text

    from mailr.core import jobs
    from mailr.db import session_scope

    users = json.loads(Path(a.users_file).read_text())
    importer = next(u for u in users if u["importer"])
    others = [u for u in users if not u["importer"]]
    work = Path(a.workdir)
    env_extra = {"MAILR_DATA_DIR": str(work / "data"), "MAILR_LOG_LEVEL": "WARNING",
                 "LOADTEST_SEND_SECONDS": "0.05"}
    if a.legacy:
        env_extra.update(LOADTEST_LEGACY="1", MAILR_MODEL_CONCURRENCY="0", MAILR_UPSTREAM_CONCURRENCY="1")
    db_url = os.environ["MAILR_DATABASE_URL"]
    procs: list[subprocess.Popen] = []
    logs = []

    def start(name: str, cmd: list[str]) -> None:
        f = open(work / f"{name}.log", "w")  # noqa: SIM115
        logs.append(f)
        procs.append(subprocess.Popen(cmd, cwd=BACKEND, env=_env(db_url, **env_extra), stdout=f, stderr=f,
                                      start_new_session=True))

    start("api", [str(BACKEND / ".venv" / "bin" / "mailr"), "serve", "--host", "127.0.0.1", "--port", str(a.api_port)])
    base = f"http://127.0.0.1:{a.api_port}"
    lat: dict[str, list[float]] = {"list": [], "search": []}
    made = {"mail": 0, "send": 0, "burst": 0, "ui_errors": 0}
    try:
        async with httpx.AsyncClient(base_url=base, timeout=60) as probe:
            for _ in range(120):
                try:
                    r = await probe.get("/api/v1/conversations", params={"view": "inbox"},
                                        cookies={"mailr_session": users[0]["token"]})
                    if r.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
            else:
                raise SystemExit("the API did not start; see api.log")
        print("api up", file=sys.stderr, flush=True)
        for w in range(a.workers):
            start(f"worker{w}", [sys.executable, str(HERE / "worker.py")])
        await asyncio.sleep(a.warmup)

        async with session_scope() as db:
            await db.execute(text("DELETE FROM loadtest_events"))
            left0 = (await db.execute(text("SELECT count(*) FROM messages WHERE user_id = :u AND intel_state = "
                                           "'backfill'"), {"u": importer["id"]})).scalar_one()
        t_start = time.time()
        async with session_scope() as db:
            await jobs.enqueue(db, "intel.backfill", {"user_id": importer["id"]}, user_id=importer["id"],
                               dedupe_key=f"intel.backfill:{importer['id']}")
        for i in range(0, a.burst, 250):
            async with session_scope() as db:
                for _ in range(min(250, a.burst - i)):
                    await new_mail(db, importer, t_start)
                    made["burst"] += 1
        stop_at = time.time() + a.duration
        print(f"burst of {made['burst']} queued; load for {a.duration:.0f}s", file=sys.stderr, flush=True)

        async def mail_loop(u):
            while time.time() < stop_at:
                await asyncio.sleep(min(stop_at - time.time(), random.expovariate(1 / a.mail_every)))
                if time.time() >= stop_at:
                    return
                async with session_scope() as db:
                    await new_mail(db, u)
                made["mail"] += 1

        async def send_loop(u):
            while time.time() < stop_at:
                await asyncio.sleep(min(stop_at - time.time(), random.expovariate(1 / a.send_every)))
                if time.time() >= stop_at:
                    return
                async with session_scope() as db:
                    await jobs.enqueue(db, "outbox.send", {"outbox_id": 0, "loadtest": True, "t0": time.time()},
                                       user_id=u["id"])
                made["send"] += 1

        async def ui_loop(u, c: httpx.AsyncClient):
            n = 0
            while time.time() < stop_at:
                await asyncio.sleep(min(stop_at - time.time(), random.expovariate(1 / a.ui_every)))
                if time.time() >= stop_at:
                    return
                n += 1
                search = n % 3 == 0
                params = {"view": "all", "q": random.choice(["quarterly", "from:sender42", "lorem w17"])} \
                    if search else {"view": "inbox"}
                t = time.perf_counter()
                try:
                    r = await c.get("/api/v1/conversations", params=params)
                    ok = r.status_code == 200
                except httpx.HTTPError:
                    ok = False
                if ok:
                    lat["search" if search else "list"].append((time.perf_counter() - t) * 1000)
                else:
                    made["ui_errors"] += 1

        clients = [httpx.AsyncClient(base_url=base, timeout=120, cookies={"mailr_session": u["token"]},
                                     headers={"X-CSRF-Token": u["csrf"]}) for u in others]
        try:
            await asyncio.gather(*(mail_loop(u) for u in others), *(send_loop(u) for u in others),
                                 *(ui_loop(u, c) for u, c in zip(others, clients, strict=True)))
        finally:
            for c in clients:
                await c.aclose()
        t_end = time.time()
        print(f"load done: {made}", file=sys.stderr, flush=True)
        async with session_scope() as db:
            left1 = (await db.execute(text("SELECT count(*) FROM messages WHERE user_id = :u AND intel_state = "
                                           "'backfill'"), {"u": importer["id"]})).scalar_one()
        # Let the measured jobs that were queued finish (the burst may not: it is reported as is).
        deadline = time.time() + a.drain
        while time.time() < deadline:
            async with session_scope() as db:
                n_mail = (await db.execute(text("SELECT count(*) FROM loadtest_events WHERE kind = 'intel.message' "
                                                "AND user_id <> :u"), {"u": importer["id"]})).scalar_one()
                n_send = (await db.execute(text("SELECT count(*) FROM loadtest_events WHERE kind = 'outbox.send'"))
                          ).scalar_one()
            if n_mail >= made["mail"] and n_send >= made["send"]:
                break
            await asyncio.sleep(1)
        async with session_scope() as db:
            ev = (await db.execute(text("SELECT kind, user_id, t0, t1, worker FROM loadtest_events"))).all()
        async with httpx.AsyncClient(timeout=10) as c:
            stub = (await c.get(a.model_url.rstrip("/") + "/stats")).json()
    finally:
        for p in procs:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for p in procs:
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
        for f in logs:
            f.close()

    def secs(kind: str, mine: bool | None = None, before: float | None = None) -> list[float]:
        return [e.t1 - e.t0 for e in ev if e.kind == kind
                and (mine is None or (e.user_id == importer["id"]) == mine)
                and (before is None or e.t1 <= before)]

    mail, send, burst = secs("intel.message", False), secs("outbox.send"), secs("intel.message", True, t_end)
    span = (t_end - t_start) / 60
    res = {
        "users": len(others), "workers": a.workers, "legacy": a.legacy, "duration_s": round(t_end - t_start),
        "new_mail": {"made": made["mail"], "done": len(mail), "p50_s": _pct(mail, 50), "p95_s": _pct(mail, 95)},
        "send": {"made": made["send"], "done": len(send), "p50_s": _pct(send, 50), "p95_s": _pct(send, 95)},
        "burst": {"made": made["burst"], "done_in_run": len(burst), "p50_s": _pct(burst, 50),
                  "p95_s": _pct(burst, 95)},
        "list_ms": {"n": len(lat["list"]), "p50": _pct(lat["list"], 50), "p95": _pct(lat["list"], 95)},
        "search_ms": {"n": len(lat["search"]), "p50": _pct(lat["search"], 50), "p95": _pct(lat["search"], 95)},
        "ui_errors": made["ui_errors"],
        "backfill_per_min": round((left0 - left1) / span) if span else None,
        "model": stub,
        "workers_seen": len({e.worker for e in ev}),
    }
    print("RESULT " + json.dumps(res), flush=True)


# ---------------- orchestration ----------------

async def _admin(url: str):
    import asyncpg

    sys.path.insert(0, str(BACKEND))
    from mailr.dev import testdb

    return await asyncpg.connect(testdb.dsn(url, "postgres"))


async def _create_from(url: str, name: str, template: str) -> None:
    conn = await _admin(url)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
    finally:
        await conn.close()


async def _drop(url: str, *names: str) -> None:
    conn = await _admin(url)
    try:
        for n in names:
            await conn.execute(f'DROP DATABASE IF EXISTS "{n}" WITH (FORCE)')
    finally:
        await conn.close()


def _row(r: dict) -> str:
    def f(x, nd=2):
        return "-" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))

    return (f"| {r['users']:>3} | {r['workers']} | {'before' if r['legacy'] else 'after'} | "
            f"{f(r['new_mail']['p50_s'])} / {f(r['new_mail']['p95_s'])} ({r['new_mail']['done']}/{r['new_mail']['made']}) | "
            f"{f(r['send']['p50_s'])} / {f(r['send']['p95_s'])} | "
            f"{f(r['list_ms']['p50'], 0)} / {f(r['list_ms']['p95'], 0)} | "
            f"{f(r['search_ms']['p50'], 0)} / {f(r['search_ms']['p95'], 0)} | "
            f"{r['burst']['done_in_run']}/{r['burst']['made']} | {r['backfill_per_min']} | "
            f"{r['model'].get('chat')} / {r['model'].get('embed')} |")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--users", default="10,50,100")
    ap.add_argument("--workers", default="1,2")
    ap.add_argument("--legacy", action="store_true", help="also run each configuration as before per-user turns")
    ap.add_argument("--only-legacy", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--conversations", type=int, default=1000, help="per user (two messages each)")
    ap.add_argument("--backfill", type=int, default=100_000, help="the importer's messages waiting for triage")
    ap.add_argument("--burst", type=int, default=2000, help="new messages for the importer at the start")
    ap.add_argument("--duration", type=float, default=120)
    ap.add_argument("--warmup", type=float, default=8)
    ap.add_argument("--drain", type=float, default=90)
    ap.add_argument("--mail-every", type=float, default=30.0)
    ap.add_argument("--send-every", type=float, default=120.0)
    ap.add_argument("--ui-every", type=float, default=10.0)
    ap.add_argument("--chat-latency", type=float, default=0.3)
    ap.add_argument("--embed-latency", type=float, default=0.05)
    ap.add_argument("--gpu-slots", type=int, default=4)
    ap.add_argument("--prefix", default="mailr_test_loadtest")
    ap.add_argument("--api-port", type=int, default=0)
    ap.add_argument("--keep-logs", action="store_true", help="keep the processes' logs (the path is printed)")
    # Internal steps, run as subprocesses.
    ap.add_argument("--step", choices=["seed", "drive"], help=argparse.SUPPRESS)
    ap.add_argument("--model-url", help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)
    ap.add_argument("--users-file", help=argparse.SUPPRESS)
    ap.add_argument("--workdir", help=argparse.SUPPRESS)
    ap.add_argument("--workers-n", type=int, help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.step == "seed":
        a.users = int(a.users)
        asyncio.run(seed(a))
        return
    if a.step == "drive":
        a.workers = a.workers_n
        asyncio.run(drive(a))
        return

    url = os.environ.get("MAILR_DATABASE_URL") or ""
    if not url:
        raise SystemExit("run under tools/agent-env (MAILR_DATABASE_URL is not set)")
    sys.path.insert(0, str(BACKEND))
    from mailr.dev import testdb

    testdb.check_server(url)
    work = Path(tempfile.mkdtemp(prefix="mailr-loadtest-"))
    print(f"logs: {work}", file=sys.stderr, flush=True)
    stub = subprocess.Popen([sys.executable, str(HERE / "stub_model.py"), "--port", "0", "--chat-latency",
                             str(a.chat_latency), "--embed-latency", str(a.embed_latency), "--slots",
                             str(a.gpu_slots)], stdout=subprocess.PIPE, text=True)
    port = int((stub.stdout.readline() if stub.stdout else "PORT 0").split()[1])
    model_url = f"http://127.0.0.1:{port}/v1"
    results = []
    seed_db, run_db = f"{a.prefix}_seed", f"{a.prefix}_run"
    try:
        for n in [int(x) for x in a.users.split(",")]:
            asyncio.run(testdb.clone(url, seed_db))
            users_file = work / f"users-{n}.json"
            subprocess.run([sys.executable, __file__, "--step", "seed", "--users", str(n), "--conversations",
                            str(a.conversations), "--backfill", str(a.backfill), "--model-url", model_url,
                            "--out", str(users_file)], env=_env(testdb.with_db(url, seed_db)), check=True,
                           cwd=BACKEND)
            modes = [True] if a.only_legacy else ([False, True] if a.legacy else [False])
            for w in [int(x) for x in a.workers.split(",")]:
                for legacy in modes:
                    asyncio.run(_create_from(url, run_db, seed_db))
                    rd = work / f"run-{n}-{w}-{int(legacy)}"
                    (rd / "data").mkdir(parents=True)
                    import socket

                    with socket.socket() as s:
                        s.bind(("127.0.0.1", a.api_port))
                        api_port = s.getsockname()[1]
                    cmd = [sys.executable, __file__, "--step", "drive", "--workers-n", str(w), "--users-file",
                           str(users_file), "--workdir", str(rd), "--model-url", model_url, "--api-port",
                           str(api_port), "--duration", str(a.duration), "--warmup", str(a.warmup), "--drain",
                           str(a.drain), "--mail-every", str(a.mail_every), "--send-every", str(a.send_every),
                           "--ui-every", str(a.ui_every), "--burst", str(a.burst)] + (["--legacy"] if legacy else [])
                    with open(rd / "drive.log", "w") as log:
                        out = subprocess.run(cmd, env=_env(testdb.with_db(url, run_db)), stdout=subprocess.PIPE,
                                             stderr=log, text=True, cwd=BACKEND)
                    line = next((x for x in out.stdout.splitlines() if x.startswith("RESULT ")), None)
                    if line is None:
                        raise SystemExit(f"run {n} users, {w} workers failed; logs in {rd}")
                    r = json.loads(line[7:])
                    results.append(r)
                    print(json.dumps(r), flush=True)
            asyncio.run(_drop(url, run_db, seed_db))
    finally:
        stub.terminate()
        asyncio.run(_drop(url, run_db, seed_db))
    print("\n| users | workers | claims | new mail → triaged p50/p95 s (done/made) | send p50/p95 s | "
          "inbox page p50/p95 ms | search p50/p95 ms | burst done | backfill msgs/min | model calls chat/embed |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        print(_row(r))
    if not a.keep_logs:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
