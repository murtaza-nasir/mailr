"""mailr command line: `mailr serve`, `mailr worker`, and admin chores."""

from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import os
import sys
from pathlib import Path


def _prepare_process() -> None:
    """Before serving: files mailr writes are not readable by other accounts on the host
    (umask 027), temporary files go under the data dir, and the configuration (core/configcheck.py)
    and the instance key must be usable."""
    from mailr.config import get_settings

    # Fail fast first on a configuration that cannot work: one line per problem, never a value.
    from mailr.core import configcheck
    from mailr.core.crypto import WeakInstanceKey, check_instance_key

    found = configcheck.problems()
    for prob in found:
        print(f"mailr: {prob.line()}", file=sys.stderr)
    if configcheck.fatal(found):
        sys.exit("mailr: the configuration has errors (above); run `mailr check-config` after fixing them")
    os.umask(0o027)
    tmp = os.path.join(get_settings().data_dir, "tmp")
    try:
        os.makedirs(tmp, mode=0o700, exist_ok=True)
        os.environ.setdefault("TMPDIR", tmp)
    except OSError:
        pass  # no data dir (a development checkout): the system temp dir
    s = get_settings()
    for root in (s.data_dir, s.backup_target):
        close_to_others(root)
    try:
        check_instance_key()
    except WeakInstanceKey as e:
        sys.exit(str(e))


def _check_config(offline: bool) -> int:
    from mailr.core import configcheck

    found = configcheck.problems()
    if not offline and not configcheck.fatal(found):
        found += _run(configcheck.online_problems())
    print(configcheck.report(found))
    return 1 if configcheck.fatal(found) else 0


def close_to_others(root: str | None, depth: int = 2) -> None:
    """Directories written before the umask was set lose their access for other accounts
    (the top `depth` levels: below them nothing is reachable without passing through)."""
    if not root or not os.path.isdir(root):
        return
    level = [root]
    for _ in range(depth):
        nxt = []
        for d in level:
            try:
                st = os.stat(d)
                if st.st_mode & 0o007:
                    os.chmod(d, st.st_mode & 0o7770)
                nxt += [e.path for e in os.scandir(d) if e.is_dir(follow_symlinks=False)]
            except OSError:
                continue
        level = nxt


def _run(coro):
    return asyncio.run(coro)


async def _create_user(email: str, name: str, admin: bool, password: str | None) -> None:
    from mailr.core.users import bootstrap, create_user
    from mailr.db import sessionmaker

    await bootstrap()
    async with sessionmaker()() as db:
        u = await create_user(db, email, name, password=password, role="admin" if admin else "user")
        print(f"created {'admin' if admin else 'user'} {u.email} (id {u.id})")


async def _set_password(email: str, password: str) -> None:
    from sqlalchemy import func, select

    from mailr.core.auth import hash_password
    from mailr.db import session_scope
    from mailr.models import Session, User

    async with session_scope() as db:
        u = (await db.execute(select(User).where(func.lower(User.email) == email.lower()))).scalar_one_or_none()
        if u is None:
            sys.exit(f"no user {email}")
        u.password_hash = hash_password(password)
        from sqlalchemy import delete

        from mailr.api.auth_routes import drop_push_targets, revoke_tokens
        from mailr.core import recovery
        from mailr.core.users import sign_out_mail_apps

        ended = (await db.execute(delete(Session).where(Session.user_id == u.id)
                                  .returning(Session.created_at))).scalars().all()
        # A reset is account recovery, as in Settings → Admin → Users.
        revoked = await revoke_tokens(db, u.id)
        await drop_push_targets(db, u.id)
        apps = await sign_out_mail_apps(u)
        await recovery.reset_channels(db, u, recovery.window_start(list(ended)))
    print(f"password updated for {email}; existing sessions signed out, {revoked} API token(s) revoked, "
          + ("mail and calendar apps NOT signed out (the mail server failed; see the log)" if apps["mail_apps_failed"]
             else f"IMAP password replaced, {apps['revoked_app_passwords']} app password(s) deleted"))


async def _token(email: str, name: str, scopes: list[str], local_only: bool) -> None:
    from sqlalchemy import func, select

    from mailr.core.auth import create_token
    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        u = (await db.execute(select(User).where(func.lower(User.email) == email.lower()))).scalar_one_or_none()
        if u is None:
            sys.exit(f"no user {email}")
        raw, t = await create_token(db, u, name, scopes, local_only=local_only)
        print(raw)


async def _web_search_key(name: str, kind: str | None, base_url: str | None, network: str | None,
                          priority: int | None, per_day: int | None, key: str, reader: bool = False) -> None:
    """Store a web search provider's (or, with `reader`, a page reader's) API key read from standard
    input (never printed); create it when nothing has this name (site/docs/admin/web-search.md)."""
    from sqlalchemy import select

    from mailr.core.crypto import encrypt_instance, web_search_provider_context
    from mailr.db import session_scope
    from mailr.models import WebSearchProvider
    from mailr.websearch import service
    from mailr.websearch.adapters import ADAPTERS
    from mailr.websearch.readers import READERS

    async with session_scope() as db:
        row = (await db.execute(select(WebSearchProvider).where(WebSearchProvider.name == name))).scalar_one_or_none()
        made = row is None
        purpose = (row.purpose if row is not None else None) or ("reader" if reader else "search")
        kinds = READERS if purpose == "reader" else ADAPTERS
        if made:
            if kind not in kinds:
                sys.exit(f"no provider named {name!r}; pass --kind ({', '.join(kinds)}) to create it")
            net = network or kinds[kind].default_network
            try:
                base = service.check_base(kind, net, base_url or kinds[kind].default_url, kinds)
                await service.check_target(net, base)
            except service.ProviderError as e:
                sys.exit(str(e))
            row = WebSearchProvider(name=name, purpose=purpose, kind=kind, base_url=base, network=net, use_egress=True,
                                    priority=100 if priority is None else priority, enabled=True, max_results=10,
                                    timeout_s=30 if purpose == "reader" else 10, per_minute=0, per_day=per_day or 0)
            db.add(row)
            await db.flush()
        else:
            if kind and kind != row.kind or base_url or network:
                sys.exit("the provider exists: change its service, address or network in Settings → Admin → Web "
                         "search (this command only sets the key)")
            if priority is not None:
                row.priority = priority
            if per_day is not None:
                row.per_day = per_day
        if key:
            row.api_key_enc = encrypt_instance(key, context=web_search_provider_context(row.id))
        elif kinds[row.kind].needs_key:
            sys.exit(f"{kinds[row.kind].label} needs an API key on standard input")
    said = f"API key stored ({len(key)} characters)" if key else "no API key"
    what = "page reader" if purpose == "reader" else "web search provider"
    print(f"{'created' if made else 'updated'} {what} {name!r} (id {row.id}, {row.kind}, {row.base_url}); {said}")


def _app_publish(app: str, rollback: bool, allow_older: bool) -> None:
    """Publish an Android app's APK read from standard input, or go back to the previous version
    (core/phone_apps.py, site/docs/admin/phone-apps.md). Audited in the first admin's audit log."""
    from mailr.core import phone_apps

    try:
        if rollback:
            out = phone_apps.rollback(app, by="cli")
        else:
            if sys.stdin.isatty():
                sys.exit("pipe the APK in on standard input, for example: mailr app-publish --app notes < notes.apk")
            data = sys.stdin.buffer.read(phone_apps.MAX_BYTES + 1)
            out = phone_apps.publish(app, data, by="cli", allow_older=allow_older)
    except phone_apps.PublishError as e:
        sys.exit(f"refused: {e}")
    r = out["release"]
    _run(_audit_cli("admin.app_rollback" if rollback else "admin.app_publish", app,
                    {"version_name": r["version_name"], "version_code": r["version_code"], "sha256": r["sha256"],
                     "unchanged": out["unchanged"]}))
    if out["unchanged"]:
        print(f"{r['name']} {r['version_name']} ({r['version_code']}) is already the published version")
        return
    old = out["replaced"]
    print(f"{'restored' if rollback else 'published'} {r['name']} {r['version_name']} ({r['version_code']}), "
          f"{r['size']} bytes, sha256 {r['sha256']}")
    if old:
        print(f"kept as the previous version: {old.get('version_name') or '?'} ({old.get('version_code')})")
    print(f"phones download it at {r['url']}")


async def _audit_cli(action: str, target: str, result: dict) -> None:
    """A command-line admin action, in the audit log of the oldest admin account (actor "cli").
    Nothing is written when the server has no admin yet."""
    from sqlalchemy import select

    from mailr.db import session_scope
    from mailr.models import AuditLog, User

    async with session_scope() as db:
        uid = (await db.execute(select(User.id).where(User.role == "admin").order_by(User.id).limit(1))).scalar()
        if uid is not None:
            db.add(AuditLog(user_id=uid, token_id=None, actor="cli", action=action, target=target, inputs={},
                            result=result, ok=True))


async def _rethread(email: str) -> None:
    from sqlalchemy import func, select

    from mailr.core import threading
    from mailr.db import session_scope
    from mailr.models import User

    async with session_scope() as db:
        u = (await db.execute(select(User).where(func.lower(User.email) == email.lower()))).scalar_one()
        n = await threading.rethread_all(db, u.id)
    print(f"rethreaded {n} messages")


async def _rethread_automated(who: str, dry_run: bool, sample: int) -> None:
    from sqlalchemy import func, select

    from mailr.core import rethread_automated
    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        q = select(User).where(User.id == int(who)) if who.isdigit() else \
            select(User).where(func.lower(User.email) == who.lower())
        u = (await db.execute(q)).scalar_one_or_none()
        if u is None:
            sys.exit(f"no user {who}")
    report = await rethread_automated.run(u.id, dry_run=dry_run)
    print(rethread_automated.describe(report, dry_run=dry_run, sample=sample))


async def _reembed(email: str | None, inline: bool) -> None:
    """Build missing passages and embed everything not embedded by the current model.
    Default: queue the worker job per user. --inline runs it here (progress printed)."""
    import time

    from sqlalchemy import func, select

    from mailr.core.jobs import enqueue
    from mailr.db import session_scope
    from mailr.intel import embed, llm, passages
    from mailr.models import User

    async with session_scope() as db:
        if await embed.current(db, sensitive=False) is None:
            sys.exit("no embedding endpoint is configured (admin model endpoint with an 'embed' default)")
        q = select(User).where(User.disabled.is_(False))
        if email:
            q = q.where(func.lower(User.email) == email.lower())
        users = list((await db.execute(q)).scalars())
        if not users:
            sys.exit(f"no user {email}")
        if not inline:
            for u in users:
                await enqueue(db, "intel.embed_pending", {"user_id": u.id}, user_id=u.id,
                              dedupe_key=f"intel.embed_pending:{u.id}")
            print(f"queued re-embedding for {len(users)} user(s); the worker does the rest")
            return
    for u in users:
        t0, total_p, total_e = time.monotonic(), 0, 0
        before, passages_done = None, False
        while True:
            async with session_scope() as db:
                built = 0
                if not passages_done:
                    for m in await passages.messages_without_passages(db, u.id, limit=500):
                        built += await passages.build_for_message(db, m)
                    passages_done = built == 0
                try:
                    res = await embed.embed_pending_passages(db, u.id, limit=2048, before=before)
                    before = res["before"] if res["before"] is not None else before
                except llm.EmbedUnavailable as e:
                    sys.exit(f"embedding endpoint failed: {e}")
            total_p += built
            total_e += res["pending"]
            rate = total_e / max(0.001, time.monotonic() - t0)
            print(f"{u.email}: {total_p} passages built, {total_e} embedded ({rate:.0f}/s)", flush=True)
            if built == 0 and res["pending"] < 2048:
                break


async def _pick_users(who: str | None) -> list[tuple[int, str]]:
    """(id, email) of one user (email or id) or, with no argument, every enabled user."""
    from sqlalchemy import func, select

    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        q = select(User.id, User.email).where(User.disabled.is_(False)).order_by(User.id)
        if who:
            q = select(User.id, User.email).where(User.id == int(who)) if who.isdigit() else \
                select(User.id, User.email).where(func.lower(User.email) == who.lower())
        users = [(r[0], r[1]) for r in (await db.execute(q)).all()]
    if not users:
        sys.exit(f"no user {who}")
    return users


async def _resnippet(who: str | None, days: int, batch: int, dry_run: bool) -> None:
    from mailr.core import reparse

    for uid, email in await _pick_users(who):
        def show(st, email=email):
            print(f"{email}: {st['seen']} read, {st['changed']} previews {'would change' if dry_run else 'changed'}, "
                  f"{st['skipped']} skipped, {st['failed']} failed", flush=True)

        st = await reparse.resnippet(uid, days=days, batch=batch, dry_run=dry_run, progress=show)
        for ex in st["examples"]:
            print(f"  #{ex['id']}\n    before: {ex['before']}\n    after:  {ex['after']}")


async def _langid_backfill(who: str | None, days: int, batch: int, redo: bool) -> None:
    from mailr.core import reparse

    for uid, email in await _pick_users(who):
        def show(st, email=email):
            print(f"{email}: {st['seen']} read, {st['detected']} with a language, {st['short']} too short, "
                  f"{st['failed']} failed", flush=True)

        try:
            st = await reparse.langid_backfill(uid, days=days, batch=batch, redo=redo, progress=show)
        except RuntimeError as e:
            sys.exit(str(e))
        top = sorted(st["languages"].items(), key=lambda kv: -kv[1])[:10]
        if top:
            print("  " + ", ".join(f"{k} {v}" for k, v in top))


async def _share_origin_backfill(who: str | None, since: str | None, batch: int, dry_run: bool) -> None:
    from datetime import UTC, datetime

    from mailr.core import reparse

    start = reparse.SHARE_REPAIR_SINCE
    if since:
        try:
            start = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            sys.exit(f"--since: not an ISO date or time: {since}")
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
    for uid, email in await _pick_users(who):
        def show(st, email=email):
            print(f"{email}: {st['seen']} read, {st['shared']} shares from the phone, {st['sent_by_me']} "
                  f"{'to mark' if dry_run else 'marked'} sent by you, {st['to_self']} notes to self"
                  + ("" if dry_run else f" ({st['filtered']} matched a filter)") + f", {st['failed']} failed",
                  flush=True)

        st = await reparse.share_origin_backfill(uid, since=start, batch=batch, dry_run=dry_run, progress=show)
        for ex in st["examples"]:
            print(f"  #{ex['id']} {ex['subject']!r} sent_by_me={ex['sent_by_me']} filters={ex['filters']}")


async def _calendar_fix_zones(who: str | None, dry_run: bool) -> None:
    """`mailr calendar-fix-zones`: counts only, per user (calendar/fix_zones.py)."""
    from mailr.calendar import fix_zones
    from mailr.db import sessionmaker

    failed = 0
    for uid, email in await _pick_users(who):
        async with sessionmaker()() as db:
            st = await fix_zones.fix_user(db, uid, dry_run=dry_run)
        failed += st["failed"]
        print(fix_zones.line(email, st, dry_run), flush=True)
    if dry_run:
        print("dry run, nothing changed")
    if failed:
        sys.exit(1)


async def _address_clean_backfill(who: str | None, batch: int, dry_run: bool) -> None:
    """`mailr address-clean-backfill`: counts only, per user (core/address_backfill.py)."""
    from mailr.core import address_backfill
    from mailr.db import sessionmaker

    for uid, email in await _pick_users(who):
        async with sessionmaker()() as db:
            st = await address_backfill.clean_user(db, uid, batch=batch, dry_run=dry_run)
        print(address_backfill.line(email, st, dry_run), flush=True)
    if dry_run:
        print("dry run, nothing changed")


async def _auth_results_backfill(who: str | None, days: int, batch: int, dry_run: bool) -> None:
    from mailr.core import reparse

    for uid, email in await _pick_users(who):
        def show(st, email=email):
            print(f"{email}: {st['seen']} read, {st['changed']} {'would change' if dry_run else 'changed'}, "
                  f"{st['not_mine']} no longer sent by you, {st['failed']} failed", flush=True)

        st = await reparse.auth_results_backfill(uid, days=days, batch=batch, dry_run=dry_run, progress=show)
        for ex in st["examples"]:
            print(f"  #{ex['id']} {ex['subject']!r} from {ex['from']}: not sent by you")


async def _reparse_attached_emails(who: str | None, batch: int, rate: float, dry_run: bool, restart: bool) -> None:
    """Counts only, per user and mail account: never a subject, an address or any text."""
    from mailr.core import reparse

    for uid, email in await _pick_users(who):
        def show(st, email=email):
            print(f"{email}: {st['seen']} read, {st['with_attached']} with attached emails, {st['changed']} "
                  f"{'would change' if dry_run else 'changed'}, {st['failed']} failed", flush=True)

        st = await reparse.attached_emails(uid, batch=batch, rate=rate, dry_run=dry_run, restart=restart,
                                           progress=show)
        if st["resumed_from"]:
            print(f"  resumed after message {st['resumed_from']} (--restart starts again)")
        show(st)
        for acct, n in sorted(st["accounts"].items(), key=lambda kv: int(kv[0])):
            print(f"  account {acct if acct != '0' else '(none)'}: {n} {'would change' if dry_run else 'changed'}")
        if not dry_run:
            print(f"  {st['memories_withdrawn']} suggested memories withdrawn")
            print(f"  {st.get('signals_cleared', 0)} deadlines, reply signals, action chips and move cards from the "
                  "old text dropped")
    if not dry_run:
        await _audit_cli("mail.reparse_attached_emails", who or "all", {"users": len(await _pick_users(who))})


async def _read_page(url: str, reader: str | None) -> int:
    """`mailr read-page`: one page through the page readers, as Ambrose reads it (websearch/readers.py).
    Prints the reader, the route, the HTTP status, the title and the character count, or the error;
    never the page's text or a key. Exit code 1 when the page could not be read."""
    from mailr.db import session_scope
    from mailr.websearch import pages, readers, service

    routes = {"egress": "through the egress proxy", "direct": "directly from this network",
              "third-party": "by the reader service, from its own address"}
    async with session_scope() as db:
        try:
            if reader is None:
                page = await readers.read(db, url)
            elif reader.strip().lower() in (readers.BUILTIN.lower(), "builtin"):
                page = await pages.read(db, url)
                page["reader"] = readers.BUILTIN
            else:
                rows = await service.providers(db, enabled_only=False, purpose="reader")
                row = next((r for r in rows if r.name.lower() == reader.strip().lower()), None)
                if row is None:
                    names = ", ".join([readers.BUILTIN] + [r.name for r in rows])
                    print(f"error: no page reader named {reader!r} (readers: {names})")
                    return 1
                if not row.enabled:
                    print(f"note: {row.name} is turned off; read with it anyway")
                if not url.strip().lower().startswith("https://"):
                    raise pages.PageError("only https:// pages are read")
                page = await readers.read_with(db, row, url.strip(), count_usage=False)
        except (pages.PageError, service.ProviderError) as e:
            print("error: " + "".join(ch for ch in str(e) if ch.isprintable()))
            return 1
    print(f"reader: {page.get('reader') or readers.BUILTIN}")
    print(f"route:  {routes.get(page.get('route') or '', page.get('route') or 'unknown')}")
    print(f"status: {page.get('status') or 'not reported'}")
    title = "".join(ch for ch in " ".join(str(page.get("title") or "").split()) if ch.isprintable())
    print(f"title:  {title[:200]}")  # the page's own text: no terminal control characters
    print(f"chars:  {page.get('chars', 0)}")
    return 0


async def _embed_model_backfill(who: str | None, batch: int) -> None:
    from mailr.intel import embed

    users = [(None, "every user")] if not who else await _pick_users(who)
    for uid, email in users:
        def show(st, email=email):
            print(f"{email}: {st['stamped']} vectors recorded as the current model's, {st['skipped']} left for "
                  f"the re-embedding (a passage of another model)", flush=True)

        try:
            st = await embed.backfill_message_models(uid, batch=batch, progress=show)
        except LookupError as e:
            sys.exit(str(e))
        if not st["batches"]:
            show(st)
        print(f"  model: {st['model']}")


async def _calendars(a: argparse.Namespace) -> None:
    """`mailr calendars export | restore | resync | migrate` (calendar/backup.py, calendar/restore.py,
    calendar/migrate.py)."""
    from mailr.calendar import backup as cal_backup
    from mailr.calendar import restore as cal_restore

    if a.action == "migrate":
        await _calendars_migrate(a)
    elif a.action == "export":
        if a.user:
            users = [uid for uid, _ in await _pick_users(a.user)]
        elif a.groups:
            users = []
        else:
            from sqlalchemy import select

            from mailr.db import session_scope
            from mailr.models import User

            async with session_scope() as db:
                users = list((await db.execute(select(User.id).where(User.disabled.is_(False))
                                               .order_by(User.id))).scalars())
        for uid in users:
            r = await cal_backup.export_user(uid)
            print(f"user {uid}: " + (f"snapshot {r['snapshot']}" if r.get("snapshot") else "no change")
                  + (f", copied {r['copied']}" if r.get("copied") else ""), flush=True)
        if not a.user:
            r = await cal_backup.export_groups()
            print("group calendars: " + (f"snapshot {r['snapshot']}" if r.get("snapshot") else "no change")
                  + (f", copied {r['copied']}" if r.get("copied") else ""), flush=True)
        print(f"written to {cal_backup.root()}")
    elif a.action == "restore":
        rep = await cal_restore.restore(a.source, user=a.user, groups_only=a.groups, dry_run=a.dry_run,
                                        overwrite=a.overwrite)
        head = "dry run, nothing changed" if a.dry_run else "restored"
        for o in rep.owners:
            print(f"{o['owner']} (snapshot {o['snapshot']}): {o['counts'].line()}")
        print(f"{head}: {rep.total.line()}")
        if not a.dry_run:
            for c in rep.conflicts[:50]:
                print(f"  conflict (left as it is on the server): {c}")
            if rep.connectors:
                print(f"full resync queued for {len(rep.connectors)} calendar accounts")
        for e in rep.errors[:50]:
            print(f"  error: {e}", file=sys.stderr)
        if rep.errors:
            sys.exit(1)
    elif a.action == "resync":
        if not a.all and not a.user:
            sys.exit("give --all or --user")
        ids = await cal_restore.connectors_for(user=a.user, include_external=a.include_external)
        if a.dry_run:
            print(f"dry run: {len(ids)} calendar accounts would resync in full")
            return
        n = await cal_restore.force_resync(ids)
        print(f"full resync queued for {n} calendar accounts; the worker runs them")


async def _export(a: argparse.Namespace) -> int:
    """`mailr export`: the exports the worker runs daily, now (core/backup.py, notes/backup.py,
    calendar/backup.py). Exit 1 when a mail export could not catch up, or when --mail was asked for
    while the mail export is off (MAILR_MAIL_EXPORT=false; the other exports still run)."""
    from mailr.calendar import backup as cal_backup
    from mailr.config import get_settings
    from mailr.core import backup
    from mailr.notes import backup as notes_backup

    kinds = {k for k, on in (("mail", a.mail), ("notes", a.notes), ("calendars", a.calendars)) if on} \
        or {"mail", "notes", "calendars"}
    failed = 0
    if "mail" in kinds and not get_settings().mail_export:
        print("the mail export is off (MAILR_MAIL_EXPORT=false): no mail is exported"
              + ("" if a.mail else "; notes, tables and calendars are"), file=sys.stderr if a.mail else sys.stdout,
              flush=True)
        kinds.discard("mail")
        failed += bool(a.mail)
    for uid, email in await _pick_users(a.user):
        if "mail" in kinds:
            n, done = await backup.export_until_caught_up(uid)
            print(f"{email}: mail {n} messages written" + ("" if done else ", NOT caught up (see the log)"),
                  flush=True)
            failed += not done
        if "notes" in kinds:
            r = await notes_backup.export_user(uid)
            print(f"{email}: notes and tables " + (f"snapshot {r['snapshot']}" if r.get("snapshot") else "no change")
                  + (f", copied {r['copied']}" if r.get("copied") else ""), flush=True)
        if "calendars" in kinds:
            r = await cal_backup.export_user(uid)
            print(f"{email}: calendars " + (f"snapshot {r['snapshot']}" if r.get("snapshot") else "no change")
                  + (f", copied {r['copied']}" if r.get("copied") else ""), flush=True)
    if "calendars" in kinds and not a.user:
        r = await cal_backup.export_groups()
        print("group calendars: " + (f"snapshot {r['snapshot']}" if r.get("snapshot") else "no change")
              + (f", copied {r['copied']}" if r.get("copied") else ""), flush=True)
    if kinds:
        print(f"written to {backup.export_root(0).parent} (mail in user-<id>, notes, calendars)")
    return 1 if failed else 0


async def _purge_deleted(who: str | None, dry_run: bool, exports: bool = False) -> int:
    """`mailr purge-deleted`: what deleted mail left behind (core/purge.py), now: the text of
    deleted rows, also when that is a large part of a user's mail (the worker's daily run holds
    back then); with `exports`, also the export files of all deleted messages, those that only
    vanished from the mail store included. Exit 1 when an export was busy."""
    from sqlalchemy import select

    from mailr.core import purge
    from mailr.core.jobs import Retry
    from mailr.db import sessionmaker
    from mailr.models import User

    if who:
        users = await _pick_users(who)
    else:
        async with sessionmaker()() as db:
            users = [(r[0], r[1]) for r in (await db.execute(select(User.id, User.email).order_by(User.id))).all()]
    failed = 0
    for uid, email in users:
        try:
            r = await purge.sweep_user(uid, dry_run=dry_run, force=True, exports=exports)
        except Retry:
            print(f"{email}: the export is busy (a copy to the backup target); run this again later", flush=True)
            failed += 1
            continue
        verb = "would be" if dry_run else "were"
        line = f"{email}: {r['rows']} deleted messages {verb} scrubbed, " + (
            f"{r['files']} export files {verb} removed" if exports else
            f"{r['files_kept']} export files of deleted mail stay (--exports removes them)")
        if not dry_run and r.get("target_pending"):
            line += f" ({r['target_pending']} wait for the backup target)"
        print(line, flush=True)
    return 1 if failed else 0


async def _store_resync(who: str | None, accounts: bool) -> int:
    """`mailr store resync`: forget the store sync state of these users (core/storesync.py resync),
    so the worker compares each whole mailbox with the index at its next tick. With `accounts`, the
    users' mail accounts forget their sync position too and are scanned again from the start, as
    Resync from scratch does; mail already in Mailr is recognised and skipped."""
    from sqlalchemy import update

    from mailr.core import storesync
    from mailr.core.jobs import enqueue
    from mailr.core.mailaccounts import MAIL_ACCOUNT_KINDS
    from mailr.db import session_scope
    from mailr.models import Connector

    users = await _pick_users(who)
    await storesync.resync([u for u, _e in users])
    for uid, email in users:
        line = f"{email}: the mailbox is compared with the mail store at the worker's next store sync"
        if accounts:
            async with session_scope() as db:
                ids = (await db.execute(update(Connector).where(
                    Connector.user_id == uid, Connector.kind.in_(MAIL_ACCOUNT_KINDS), Connector.enabled.is_(True))
                    .values(state={}, status="new", status_detail="Re-scanning…").returning(Connector.id))).scalars().all()
                for cid in ids:  # a running worker restarts them (api/connector_routes.py resync)
                    await enqueue(db, "connector.sync_now", {"connector_id": cid}, user_id=uid,
                                  dedupe_key=f"connector.sync_now:{cid}")
            line += f"; {len(ids)} mail accounts are scanned again from the start"
        print(line, flush=True)
    return 0


async def _tally_rebuild(who: str | None) -> int:
    """`mailr tally rebuild`: count the users' mail for the Subscriptions and Clean up pages again
    from scratch (core/tally.py). The pages group the mail live while it runs."""
    import time

    from mailr.core import tally

    for uid, email in await _pick_users(who):
        t0 = time.monotonic()
        await tally.rebuild(uid)
        print(f"{email}: counted in {time.monotonic() - t0:.1f}s", flush=True)
    return 0


def _zip_export(src: Path, dest: Path) -> int:
    """The Maildir folders of a mail export as a zip for the file import (connectors/mbox.py),
    without the export's own records (`.exported`, `.cursor`, ...). Returns the number of messages."""
    import zipfile

    from mailr.core import private

    n = 0
    with private.open_new(dest, "wb") as f, zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
        for msg in sorted(src.glob("*/cur/*")):
            if msg.is_file() and not msg.parent.parent.name.startswith("."):
                z.write(msg, msg.relative_to(src).as_posix())
                n += 1
    return n


async def _store_import_export(who: str | None) -> int:
    """`mailr store import-export`: import each user's own mail export back, as a file import
    account, after a restore of the mail store lost mail. Messages already in Mailr are
    recognised and skipped (core/ingest.py), so only what is missing comes back."""
    import asyncio
    from datetime import UTC, datetime

    from mailr.connectors.mbox import imports_root
    from mailr.core import backup, private
    from mailr.core.jobs import enqueue
    from mailr.db import session_scope
    from mailr.models import Connector, StoreSyncState

    failed = 0
    for uid, email in await _pick_users(who):
        src = backup.export_root(uid)
        if not (src / ".exported").exists():
            print(f"{email}: no mail export", flush=True)
            continue
        async with session_scope() as db:
            st = await db.get(StoreSyncState, uid)
        if st is None or st.email_state is None:
            # Before the comparison, rows of lost mail still look present, and the import would
            # skip exactly the messages it is meant to bring back.
            print(f"{email}: the worker has not compared the mailbox with the mail store yet (mailr store "
                  "resync); start the worker, wait for its store sync, then run this again", flush=True)
            failed += 1
            continue
        dest = private.mkdirs(imports_root(uid)) / f"mail-export-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.zip"
        n = await asyncio.to_thread(_zip_export, src, dest)
        async with session_scope() as db:
            c = Connector(user_id=uid, kind="file", name="Mail export", mode="import", config={"path": str(dest)},
                          state={}, enabled=True, status="new", status_detail="Starting…")
            db.add(c)
            await db.flush()
            await enqueue(db, "connector.sync_now", {"connector_id": c.id}, user_id=uid)
        print(f"{email}: {n} exported messages queued for import (account \"Mail export\"); "
              "those already in Mailr are skipped", flush=True)
    return 1 if failed else 0


def print_migration(rep) -> None:
    """The report of `mailr calendars migrate` (calendar/migrate.py)."""
    head = "dry run, nothing changed" if rep.dry_run else "copied"
    for c in rep.collections:
        if c.action == "skipped":
            continue
        where = {"create": "new", "existing": "into existing", "previous": "into"}.get(c.action, c.action)
        n = c.counts
        print(f"{c.name} ({c.kind}) -> {where} \"{c.target_name}\": {c.total} item{'' if c.total == 1 else 's'}; "
              f"{n.created} {'to copy' if rep.dry_run else 'copied'}, {n.skipped} already there, "
              f"{n.conflicts} conflicts, {n.replaced} {'to replace' if rep.dry_run else 'replaced'}, {n.failed} failed"
              + ("; the source listed only part of it" if c.partial else ""))
        for uid in c.conflicts[:20]:
            print(f"  conflict (left as it is in the mailr calendar): {uid.split(': ', 1)[-1]}")
        for f in c.failures[:20]:
            print(f"  failed: {f.get('uid') or f.get('item') or c.name}: {f['reason']}")
    t = rep.totals()
    print(f"{head}: {t['collections']} collections ({t['created_collections']} new), {t['total']} items; "
          f"{t['copied']} {'to copy' if rep.dry_run else 'copied'}, {t['skipped']} already there, "
          f"{t['conflicts']} conflicts, {t['replaced']} {'to replace' if rep.dry_run else 'replaced'}, "
          f"{t['failed']} failed")
    if rep.not_migrated:
        print("not copied (re-create by hand):")
        for x in rep.not_migrated:
            print(f"  - {x['name']}: {x['detail']}\n      {x['how']}")
    for e in rep.errors[:50]:
        print(f"  error: {e}", file=sys.stderr)


async def _calendars_migrate(a: argparse.Namespace) -> None:
    from mailr.calendar import migrate

    user, conns = await migrate.connectors_of(a.user)
    if user is None:
        sys.exit(f"no user {a.user}")
    if a.from_connector is None:
        if not conns:
            sys.exit(f"{user.email} has no connected CalDAV account")
        print(f"{user.email}'s CalDAV accounts (give one with --from-connector):")
        for c in conns:
            print(f"  {c.id}  {c.name}  {(c.config or {}).get('url', '')}")
        sys.exit(2)
    last = [0.0]

    async def progress(done: int, total: int, name: str) -> None:
        import time

        if time.monotonic() - last[0] >= 2 or done >= total:
            last[0] = time.monotonic()
            print(f"  {done}/{total} {name}", flush=True)

    try:
        rep = await migrate.migrate(user.id, a.from_connector,
                                    collections=[x for x in (a.collections or "").split(",") if x.strip()] or None,
                                    dry_run=a.dry_run, overwrite=a.overwrite, progress=progress)
    except migrate.MigrationError as e:
        sys.exit(str(e))
    await migrate.record(user.id, rep, [x for x in (a.collections or "").split(",") if x.strip()])
    print_migration(rep)
    if not a.dry_run and (a.make_default or a.hide_copied) and not rep.errors:
        out = await migrate.cutover(user.id, a.from_connector, make_default=a.make_default, hide_source=a.hide_copied)
        if out.get("defaults"):
            print(f"defaults: calendar {out['defaults'].get('calendar')}, task list {out['defaults'].get('task_list')}")
        if a.hide_copied:
            print(f"hidden in Calendar: {out['hidden']} calendars of the other account")
    if rep.errors:
        sys.exit(1)


async def _sent_backfill(a: argparse.Namespace) -> int:
    """`mailr sent-backfill` (connectors/sentcopy.py `backfill`): run here, not in the worker. A run
    that stopped is simply run again: what it copied is linked and skipped."""
    from datetime import date

    from sqlalchemy import func, select

    from mailr.connectors import sentcopy
    from mailr.db import session_scope
    from mailr.models import Connector, User

    async with session_scope() as db:
        u = (await db.execute(select(User).where(func.lower(User.email) == a.user.lower()))).scalar_one_or_none()
        if u is None and a.user.isdigit():
            u = await db.get(User, int(a.user))
        if u is None:
            print(f"no user {a.user}", file=sys.stderr)
            return 1
        conns = list((await db.execute(select(Connector).where(Connector.user_id == u.id, Connector.kind == "imap")
                                       .order_by(Connector.id))).scalars())
    if a.account is None:
        print(f"{u.email}'s IMAP accounts (give one with --account):")
        for c in conns:
            print(f"  {c.id}  {c.name}  {c.mode}  save sent mail: {sentcopy.setting(c)}"
                  f"{' (on)' if sentcopy.active(c) else ' (off)'}")
        return 2
    try:
        since = date.fromisoformat(a.since) if a.since else None
    except ValueError:
        print(f"--since: not a date: {a.since}", file=sys.stderr)
        return 1
    last = [0.0]

    async def progress(done: int, total: int) -> None:
        import time

        if time.monotonic() - last[0] >= 2 or done >= total:
            last[0] = time.monotonic()
            print(f"  {done}/{total}", flush=True)

    try:
        n = await sentcopy.backfill(u.id, a.account, since=since, dry_run=a.dry_run, progress=progress)
    except sentcopy.BackfillError as e:
        print(f"sent-backfill: {e}", file=sys.stderr)
        return 1
    head = "dry run, nothing changed" if a.dry_run else "done"
    print(f"{head}: {n['total']} sent through this account; {n['present']} already at the provider, "
          f"{n['copied']} {'to copy' if a.dry_run else 'copied'}, {n['failed']} failed")
    return 1 if n["failed"] else 0


async def _db_dump(a: argparse.Namespace) -> int:
    from pathlib import Path

    from mailr.core import dbdump

    try:
        if a.list:
            d = Path(a.dir) if a.dir else dbdump.dump_dir()
            for f in dbdump.list_dumps(d):
                m = dbdump.read_manifest(f) or {}
                print(f"{f.name}  {dbdump.mb(f.stat().st_size)} MB  {len(m.get('tables') or {})} tables  "
                      f"revision {','.join(m.get('revision') or []) or '?'}")
            st = await dbdump.state()
            if st:
                print(f"last nightly run: {st.get('at')} {'ok' if st.get('ok') else 'FAILED: ' + str(st.get('error'))}")
            return 0
        m = await dbdump.dump(a.reason, Path(a.dir) if a.dir else None)
    except (dbdump.DumpError, ValueError) as e:
        print(f"db-dump: {e}", file=sys.stderr)
        return 1
    print(f"{m['path']}  {dbdump.mb(m['bytes'])} MB  {len(m['tables'])} tables, "
          f"{sum(m['tables'].values())} rows  {m['seconds']} s")
    return 0


async def _db_restore(a: argparse.Namespace) -> int:
    from pathlib import Path

    from mailr.core import dbdump

    try:
        if a.check:
            out = await dbdump.restore_check(Path(a.file) if a.file else None, keep=a.keep)
        else:
            if not a.file or not a.into:
                print("db-restore: give the dump file and --into <new database>, or --check", file=sys.stderr)
                return 2
            out = await dbdump.restore(Path(a.file), a.into)
    except dbdump.DumpError as e:
        print(f"db-restore: {e}", file=sys.stderr)
        return 1
    print(f"{out['file']} -> {out['database']}{' (dropped again)' if a.check and not a.keep else ''}: "
          f"{len(out['tables'])} tables, {sum(out['tables'].values())} rows, revision {','.join(out['revision'])}")
    if out["missing_manifest"]:
        print("no manifest beside the dump: row counts not compared")
    for name in out.get("unknown") or []:
        print(f"  NOT CHECKED {name}: newer, but not written by this server (no checksum recorded)")
    for t, (want, got) in out["mismatches"].items():
        print(f"  MISMATCH {t}: dump {want}, restored {got}")
    if out["mismatches"] or out["missing_manifest"]:
        return 1
    print("every table has the row count of the dump")
    return 0


def _optional_commands(sub) -> dict:
    """Commands of the optional modules in mailr/cli_ext that are present (see its __init__)."""
    import importlib
    import pkgutil

    from mailr import cli_ext

    handlers: dict = {}
    for m in sorted(pkgutil.iter_modules(cli_ext.__path__), key=lambda m: m.name):
        mod = importlib.import_module(f"mailr.cli_ext.{m.name}")
        handlers.update(mod.register(sub))
    return handlers


def main() -> None:
    ap = argparse.ArgumentParser(prog="mailr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the API + web UI")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8025")))
    s.add_argument("--workers", type=int, default=1)
    sub.add_parser("worker", help="run the background worker")
    sub.add_parser("browser-gate", help="run the browser gate: the page-reading browser's only way out (the "
                                        "compose service browser-gate; websearch/gate.py)")
    sub.add_parser("migrate", help="apply database migrations")
    cc = sub.add_parser("check-config", help="check the configuration (environment) and print one line per problem; "
                                             "exit 1 on errors")
    cc.add_argument("--offline", action="store_true", help="skip the checks that need the database and the mail store")
    sub.add_parser("gen-secret", help="print a random secret for MAILR_SECRET_KEY")
    cu = sub.add_parser("create-user")
    cu.add_argument("email")
    cu.add_argument("--name", default="")
    cu.add_argument("--admin", action="store_true")
    cu.add_argument("--no-password", action="store_true", help="OIDC-only account")
    sp = sub.add_parser("set-password")
    sp.add_argument("email")
    tk = sub.add_parser("create-token", help="mint an API token (prints it once)")
    tk.add_argument("email")
    tk.add_argument("--name", default="cli")
    tk.add_argument("--scopes", default="read,draft")
    tk.add_argument("--local-only", action="store_true")
    wk = sub.add_parser("web-search-key", help="store a web search provider's API key, read from standard input "
                                               "(never printed); creates the provider with --kind")
    wk.add_argument("name", help="the provider's name (as in Settings → Admin → Web search)")
    wk.add_argument("--kind", help="exa, brave, tavily, degoog, searxng, jina or firecrawl; with --reader: "
                                   "browserless, firecrawl or jina (only to create it)")
    wk.add_argument("--reader", action="store_true", help="a page reader, not a search provider (only to create it)")
    wk.add_argument("--base-url", help="only to create it; empty: the service's own address")
    wk.add_argument("--network", choices=("internet", "local"), help="only to create it")
    wk.add_argument("--priority", type=int, help="its place in the order (lower is tried first)")
    wk.add_argument("--per-day", type=int, help="searches a day (0: no limit)")
    pub = sub.add_parser("app-publish", help="publish an Android app's release APK, read from standard input "
                                             "(phones download it from this server)")
    pub.add_argument("--app", required=True, choices=("mailr", "notes"), help="which app the APK is")
    pub.add_argument("--allow-older", action="store_true", help="accept a lower versionCode than the one served")
    pub.add_argument("--rollback", action="store_true",
                     help="instead: make the previous version current again (no input)")
    re_ = sub.add_parser("reembed", help="build passages and (re-)embed mail with the current embedding model")
    re_.add_argument("email", nargs="?")
    re_.add_argument("--inline", action="store_true", help="run here instead of queueing worker jobs")
    e2e = sub.add_parser("e2e-seed", help="development only: a fresh user with known mail for the browser tests")
    e2e.add_argument("--extras", action="store_true", help="also follow-ups, a second task list and a week of events")
    e2e.add_argument("--bulk", type=int, default=0, help="also N plain inbox conversations (\"E2E Bulk 0\" …)")
    e2e.add_argument("--screener", action="store_true",
                     help="also the sender screener on, with first-time senders waiting in it")
    e2e.add_argument("--subscriptions", action="store_true",
                     help="also list and bulk mail for the Subscriptions and Clean up pages")
    e2e.add_argument("--accounts", action="store_true",
                     help="also a second mail account (Work) with mail through each account")
    e2e.add_argument("--access", action="store_true",
                     help="instead: an admin and a user under a restricted access policy (Admin → Access)")
    e2e.add_argument("--onboarding", action="store_true",
                     help="instead: a new family user who has not set up yet (the setup guide)")
    e2e.add_argument("--model-url", default="", help="with --onboarding: the scripted chat model the spec serves")
    e2e.add_argument("--serve", action="store_true",
                     help="instead: answer seed requests on stdin, one JSON line each (a browser test worker's seeder)")
    try:
        from mailr.dev.demo import add_arguments as demo_arguments
    except ImportError:  # the demo tooling is development-only and may not be installed
        demo_arguments = None
    if demo_arguments is not None:
        demo_arguments(sub.add_parser("demo-seed", help="development only: reset and seed the fictional demo "
                                                         "instance"))
    rt = sub.add_parser("rethread", help="rebuild conversations for a user")
    rt.add_argument("email")
    ra = sub.add_parser("rethread-automated",
                        help="split conversations that group separate automated emails by subject")
    ra.add_argument("user", help="email or user id")
    ra.add_argument("--dry-run", action="store_true", help="only count and list them")
    ra.add_argument("--sample", type=int, default=10, help="how many to list")
    rs = sub.add_parser("resnippet", help="recompute the list previews of recent mail (after a preview-rule change)")
    rs.add_argument("user", nargs="?", help="email or user id (default: every enabled user)")
    rs.add_argument("--days", type=int, default=30, help="mail from the last N days (default 30)")
    rs.add_argument("--batch", type=int, default=200, help="messages per transaction (default 200)")
    rs.add_argument("--dry-run", action="store_true", help="only count and show examples")
    lb = sub.add_parser("langid-backfill", help="detect the language of recent mail stored before language identification")
    lb.add_argument("user", nargs="?", help="email or user id (default: every enabled user)")
    lb.add_argument("--days", type=int, default=90, help="mail from the last N days (default 90)")
    lb.add_argument("--batch", type=int, default=200, help="messages per transaction (default 200)")
    lb.add_argument("--redo", action="store_true", help="check messages that already have a language too")
    so = sub.add_parser("share-origin-backfill",
                        help="repair phone shares (\"Send to self\") stored by older versions: origin, "
                             "sent_by_me and filters")
    so.add_argument("user", nargs="?", help="email or user id (default: every enabled user)")
    so.add_argument("--since", help="ingested at or after this ISO date/time (default 2026-10-03T10:35Z, "
                                    "when older versions began storing them)")
    so.add_argument("--batch", type=int, default=200, help="messages per transaction (default 200)")
    so.add_argument("--dry-run", action="store_true", help="only count and list them")
    fz = sub.add_parser("calendar-fix-zones",
                        help="rewrite calendar items stored with a Windows zone name (\"Central Standard Time\", "
                             "from Outlook) on the calendar server with the IANA name; counts only")
    fz.add_argument("user", nargs="?", help="email or user id (default: every enabled user)")
    fz.add_argument("--dry-run", action="store_true", help="only count them")
    acb = sub.add_parser("address-clean-backfill",
                         help="remove bidi, invisible and control characters from addresses and names stored "
                              "before the October 2026 fix (mail, conversations, the calendar mirror); counts only")
    acb.add_argument("user", nargs="?", help="email or user id (default: every enabled user)")
    acb.add_argument("--batch", type=int, default=500, help="rows per transaction (default 500)")
    acb.add_argument("--dry-run", action="store_true", help="only count them")
    ab = sub.add_parser("auth-results-backfill",
                        help="re-read stored mail's Authentication-Results with the RFC 8601 parser; forged "
                             "\"From: me\" mail stops counting as sent by you")
    ab.add_argument("user", nargs="?", help="email or user id (default: every enabled user)")
    ab.add_argument("--days", type=int, default=365, help="mail from the last N days (default 365)")
    ab.add_argument("--batch", type=int, default=200, help="messages per transaction (default 200)")
    ab.add_argument("--dry-run", action="store_true", help="only count and list them")
    rae = sub.add_parser("reparse-attached-emails",
                         help="parse again the mail whose attached emails (message/rfc822) leaked into its body; "
                              "--dry-run only counts, per user and account")
    rae.add_argument("user", nargs="?", help="email or user id (default: every enabled user)")
    rae.add_argument("--batch", type=int, default=200, help="messages per transaction (default 200)")
    rae.add_argument("--rate", type=float, default=50.0, help="messages read from the store per second (default 50)")
    rae.add_argument("--dry-run", action="store_true", help="only count; change nothing")
    rae.add_argument("--restart", action="store_true", help="start from the first message, not where the last run ended")
    rp = sub.add_parser("read-page", help="read one web page through the page readers, as Ambrose would; prints the "
                                          "reader, the route, the HTTP status, the title and the length, or the "
                                          "error")
    rp.add_argument("url", help="the page (https:// only)")
    rp.add_argument("--reader", help="only this reader (its name in Admin → Web search, or Built-in); default: "
                                     "the whole chain")
    em = sub.add_parser("embed-model-backfill",
                        help="record the current embedding model on message vectors stored before mailr recorded it "
                             "(they are assumed to be the current model's)")
    em.add_argument("user", nargs="?", help="email or user id (default: every user)")
    em.add_argument("--batch", type=int, default=1000, help="messages per transaction (default 1000)")
    pd = sub.add_parser("purge-deleted", help="remove what deleted mail left behind: the text of deleted messages "
                                              "in the database (the worker does it daily, but holds back when "
                                              "most of a user's mail looks deleted)")
    pd.add_argument("user", nargs="?", help="email or user id (default: every user)")
    pd.add_argument("--dry-run", action="store_true", help="only count; change nothing")
    pd.add_argument("--exports", action="store_true",
                    help="also remove the files of all deleted mail from the mail export and the backup target, "
                         "mail that vanished from the mail store included; only with a healthy mail store")
    ex = sub.add_parser("export", help="write the exports now: mail (Maildir), notes and tables, calendars and "
                                       "contacts (the worker does them daily)")
    ex.add_argument("--user", help="email or user id (default: every enabled user, and the group calendars)")
    ex.add_argument("--mail", action="store_true", help="only the mail export (combine with --notes, --calendars); "
                    "fails while MAILR_MAIL_EXPORT=false")
    ex.add_argument("--notes", action="store_true", help="only the notes and tables export")
    ex.add_argument("--calendars", action="store_true", help="only the calendar and contact export")
    cal = sub.add_parser("calendars", help="calendar and contact backups (export, restore, resync) and moving in "
                                           "from another calendar server (migrate)")
    cal_sub = cal.add_subparsers(dest="action", required=True)
    ce = cal_sub.add_parser("export", help="write the calendar and contact export now (the worker does it daily)")
    ce.add_argument("--user", help="email or user id (default: every enabled user and the group calendars)")
    ce.add_argument("--groups", action="store_true", help="only the group calendars")
    cr = cal_sub.add_parser("restore", help="put a calendar export back into the built-in calendars")
    cr.add_argument("--from", dest="source", required=True,
                    help="an export folder: a snapshot, user-<id>, groups, exports/calendars or the backup target")
    cr.add_argument("--user", help="only this user (email or id)")
    cr.add_argument("--groups", action="store_true", help="only the group calendars")
    cr.add_argument("--overwrite", action="store_true",
                    help="where the server holds other content under the same UID, put back the export's version "
                         "(after restoring the Stalwart volume); default: keep the server's and report a conflict")
    cr.add_argument("--dry-run", action="store_true", help="read the server and print counts; change nothing")
    cm = cal_sub.add_parser("migrate", help="copy a user's connected CalDAV/CardDAV account (Davis, Nextcloud, …) "
                                            "into their mailr calendar; the account stays connected and unchanged")
    cm.add_argument("--user", required=True, help="email or user id")
    cm.add_argument("--from-connector", type=int, help="the account's connector id (without it: list them)")
    cm.add_argument("--collections", help="comma-separated names or slugs to copy (default: every calendar, task list "
                                          "and address book the user owns there)")
    cm.add_argument("--dry-run", action="store_true", help="read both sides and print the counts; change nothing")
    cm.add_argument("--overwrite", action="store_true",
                    help="where the mailr calendar holds other content under the same UID, replace it with the "
                         "account's version; default: keep it and report a conflict")
    cm.add_argument("--make-default", action="store_true",
                    help="afterwards, make the copies the default calendar and task list")
    cm.add_argument("--hide-copied", action="store_true",
                    help="afterwards, hide the account's copied calendars in Calendar (in mailr only)")
    cy = cal_sub.add_parser("resync", help="forget the sync state of calendar accounts and sync them in full "
                                           "(after restoring the Stalwart volume)")
    cy.add_argument("--all", action="store_true", help="every user's built-in calendar")
    cy.add_argument("--user", help="only this user (email or id)")
    cy.add_argument("--include-external", action="store_true", help="the users' own CalDAV accounts too")
    cy.add_argument("--dry-run", action="store_true", help="only count them")
    st = sub.add_parser("store", help="the index of the mail store (resync after restoring the Stalwart volume)")
    st_sub = st.add_subparsers(dest="action", required=True)
    sr = st_sub.add_parser("resync", help="forget the store sync state, so the worker compares each whole mailbox "
                                          "with the index (after restoring the Stalwart volume)")
    sr.add_argument("--user", help="only this user (email or id; default: every enabled user)")
    sr.add_argument("--accounts", action="store_true",
                    help="also scan the users' mail accounts again from the start (Resync from scratch), to "
                         "bring back mail the restore lost that the provider still has")
    si = st_sub.add_parser("import-export", help="import each user's mail export back (a file import account), to "
                                                 "bring back mail a restore of the mail store lost; messages "
                                                 "already in Mailr are skipped")
    si.add_argument("--user", help="only this user (email or id; default: every enabled user)")
    ty = sub.add_parser("tally", help="the mail tally behind the Subscriptions and Clean up pages")
    ty_sub = ty.add_subparsers(dest="action", required=True)
    tr = ty_sub.add_parser("rebuild", help="count the mail of the Subscriptions and Clean up pages again from "
                                           "scratch (the worker checks it every night)")
    tr.add_argument("--user", help="only this user (email or id; default: every enabled user)")
    sub.add_parser("langid-model", help="download the language-identification model (a development checkout; "
                                        "the image has it)")
    dd = sub.add_parser("db-dump", help="write a database dump now (pg_dump, custom format) with its manifest; the "
                                        "worker writes one every night")
    dd.add_argument("--dir", help="the folder (default: <backup target>/postgres, or /data/backups/postgres)")
    dd.add_argument("--reason", default="manual", help="the word in the file name (default manual; manual dumps are "
                                                       "never removed automatically)")
    dd.add_argument("--list", action="store_true", help="list the dumps and the last nightly result instead")
    sb = sub.add_parser("sent-backfill", help="copy a mirror account's past sent mail to the provider's Sent folder "
                                              "(what is there already is skipped; nothing is deleted)")
    sb.add_argument("--user", required=True, help="email or user id")
    sb.add_argument("--account", type=int, help="the account's id (without it: list the user's accounts)")
    sb.add_argument("--since", help="only mail sent on or after this date (YYYY-MM-DD; default: all)")
    sb.add_argument("--dry-run", action="store_true", help="read the Sent folder and print the counts; change nothing")
    dr = sub.add_parser("db-restore", help="load a dump into a NEW database and compare its row counts with the "
                                           "dump's manifest; --check runs the restore drill")
    dr.add_argument("file", nargs="?", help="the .dump file (with --check: default the newest)")
    dr.add_argument("--into", help="the new database's name (it must not exist; never the live database)")
    dr.add_argument("--check", action="store_true", help="restore into a scratch database, compare, drop it again")
    dr.add_argument("--keep", action="store_true", help="with --check: keep the scratch database")
    sub.add_parser("worker-alive", help="exit 0 when this container's worker ticked in the last 3 minutes and the "
                                        "mail store answers (the worker's Docker healthcheck)")
    optional = _optional_commands(sub)
    a = ap.parse_args()

    # Every command: files it writes (state files, calendar exports and their copies on the backup
    # share) are not readable by other accounts on the host, as serve and worker.
    os.umask(0o027)
    if a.cmd in ("serve", "worker"):
        _prepare_process()
    if a.cmd == "serve":
        import uvicorn

        from mailr.core import logsetup, proxies

        # Our handlers, not uvicorn's: JSON lines and the log files (core/logsetup.py). uvicorn's
        # access log is off; the request middleware writes one sanitised line per request instead
        # (route template, never the query string: the audit found search terms in the old lines).
        logsetup.configure("app")
        uvicorn.run("mailr.main:app", host=a.host, port=a.port, workers=a.workers, proxy_headers=True,
                    forwarded_allow_ips=proxies.forwarded_allow_ips(), access_log=False, log_config=None)
    elif a.cmd == "worker":
        from mailr.worker import run

        run()
    elif a.cmd == "browser-gate":
        os.umask(0o027)
        from mailr.websearch.gate import run as gate_run

        gate_run()
    elif a.cmd == "check-config":
        sys.exit(_check_config(a.offline))
    elif a.cmd == "migrate":
        from mailr.core.users import _migrate

        _run(_migrate())
    elif a.cmd == "e2e-seed":
        from mailr.dev.e2e import main as e2e_main

        e2e_main()
    elif a.cmd == "demo-seed":
        from mailr.dev.demo import run as demo_run

        demo_run(a)
    elif a.cmd == "gen-secret":
        print(base64.b64encode(os.urandom(32)).decode())
    elif a.cmd == "create-user":
        pw = None
        if not a.no_password:
            pw = getpass.getpass("password: ")
            if len(pw) < 10:
                sys.exit("password must be at least 10 characters")
        _run(_create_user(a.email, a.name, a.admin, pw))
    elif a.cmd == "set-password":
        pw = getpass.getpass("new password: ")
        if len(pw) < 10:
            sys.exit("password must be at least 10 characters")
        _run(_set_password(a.email, pw))
    elif a.cmd == "create-token":
        _run(_token(a.email, a.name, [x.strip() for x in a.scopes.split(",") if x.strip()], a.local_only))
    elif a.cmd == "web-search-key":
        # A keyless kind (degoog, SearXNG) takes an empty standard input; a keyed one refuses it.
        key = (getpass.getpass("API key: ") if sys.stdin.isatty() else sys.stdin.readline()).strip()
        _run(_web_search_key(a.name, a.kind, a.base_url, a.network, a.priority, a.per_day, key, a.reader))
    elif a.cmd == "app-publish":
        _app_publish(a.app, a.rollback, a.allow_older)
    elif a.cmd == "rethread":
        _run(_rethread(a.email))
    elif a.cmd == "rethread-automated":
        _run(_rethread_automated(a.user, a.dry_run, a.sample))
    elif a.cmd == "reembed":
        _run(_reembed(a.email, a.inline))
    elif a.cmd == "langid-backfill":
        _run(_langid_backfill(a.user, a.days, a.batch, a.redo))
    elif a.cmd == "langid-model":
        from mailr.core import langid

        print(f"{langid.fetch_model()} (sha256 {langid.MODEL_SHA256})")
    elif a.cmd == "resnippet":
        _run(_resnippet(a.user, a.days, a.batch, a.dry_run))
    elif a.cmd == "share-origin-backfill":
        _run(_share_origin_backfill(a.user, a.since, a.batch, a.dry_run))
    elif a.cmd == "calendar-fix-zones":
        _run(_calendar_fix_zones(a.user, a.dry_run))
    elif a.cmd == "address-clean-backfill":
        _run(_address_clean_backfill(a.user, a.batch, a.dry_run))
    elif a.cmd == "auth-results-backfill":
        _run(_auth_results_backfill(a.user, a.days, a.batch, a.dry_run))
    elif a.cmd == "export":
        sys.exit(_run(_export(a)))
    elif a.cmd == "purge-deleted":
        sys.exit(_run(_purge_deleted(a.user, a.dry_run, a.exports)))
    elif a.cmd == "calendars":
        _run(_calendars(a))
    elif a.cmd == "store" and a.action == "resync":
        sys.exit(_run(_store_resync(a.user, a.accounts)))
    elif a.cmd == "store" and a.action == "import-export":
        sys.exit(_run(_store_import_export(a.user)))
    elif a.cmd == "tally" and a.action == "rebuild":
        sys.exit(_run(_tally_rebuild(a.user)))
    elif a.cmd == "reparse-attached-emails":
        _run(_reparse_attached_emails(a.user, a.batch, a.rate, a.dry_run, a.restart))
    elif a.cmd == "embed-model-backfill":
        _run(_embed_model_backfill(a.user, a.batch))
    elif a.cmd == "read-page":
        sys.exit(_run(_read_page(a.url, a.reader)))
    elif a.cmd == "sent-backfill":
        sys.exit(_run(_sent_backfill(a)))
    elif a.cmd == "db-dump":
        sys.exit(_run(_db_dump(a)))
    elif a.cmd == "db-restore":
        sys.exit(_run(_db_restore(a)))
    elif a.cmd == "worker-alive":
        from mailr.core import health

        sys.exit(0 if health.worker_alive() and health.store_alive() else 1)
    elif a.cmd in optional:
        optional[a.cmd](a)


if __name__ == "__main__":
    main()
