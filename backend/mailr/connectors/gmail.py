"""Gmail connector (IMAP with Gmail extensions).

Gmail is label-based, so mailr syncs "[Gmail]/All Mail" once and reads each message's
labels (X-GM-LABELS) and thread id (X-GM-THRID), plus Spam and Trash, which All Mail
excludes. Categories are not IMAP labels; they are found with X-GM-RAW "category:<name>"
searches before each All Mail pass.

Sign-in uses an app password today; `secrets["oauth"]["access_token"]` switches to XOAUTH2.
Modes: import (default) and mirror (labels and flags written back with X-GM-LABELS STORE).
"""

from __future__ import annotations

import logging
from typing import Any

from imapclient import imap_utf7

from mailr.connectors.base import Capabilities, TestResult, WriteBack, register
from mailr.connectors.imap import (
    ImapConnector,
    Target,
    _aware,
    all_refs,
    keywords_from_flags,
    pulled_since,
    refs_to_upstream,
)

log = logging.getLogger("mailr.gmail")

CATEGORIES = ("social", "promotions", "updates", "forums", "purchases")
SYSTEM = {"\\inbox": ("role", "inbox"), "\\sent": ("role", "sent")}
IGNORED = {"\\important", "\\starred", "\\draft", "\\drafts", "\\chats", "\\muted", "\\spam", "\\trash",
           "\\all", "\\allmail", "\\flagged"}
ALL_MAIL = ("gmail", "all")


def decode_label(v: Any) -> str:
    if isinstance(v, bytes):
        try:
            return imap_utf7.decode(v)
        except Exception:  # noqa: BLE001
            return v.decode("utf-8", "replace")
    return str(v)


def map_gmail_labels(labels: list[str]) -> dict[str, Any]:
    """Gmail labels → ingest arguments. Returns None-able fields; see GmailConnector."""
    roles: list[str] = []
    names: list[str] = []
    kw = {"important": False, "flagged": False}
    draft = False
    for raw in labels:
        lab = raw.strip('"')
        low = lab.lower()
        if low in SYSTEM:
            roles.append(SYSTEM[low][1])
        elif low == "\\important":
            kw["important"] = True
        elif low == "\\starred":
            kw["flagged"] = True
        elif low in ("\\draft", "\\drafts"):
            draft = True
        elif low in IGNORED or low.startswith("\\") or low.startswith("category_") or low.startswith("category/") or low.startswith("[imap]/"):
            continue
        else:
            names.append(lab)
    return {"roles": roles, "label_names": names, "keywords": kw, "draft": draft}


@register
class GmailConnector(ImapConnector):
    kind = "gmail"
    capabilities = Capabilities(push=True, send=True, write_back=True, delete=True, labels=True,
                                modes=("import", "mirror", "store"))

    def __init__(self, row, secrets):
        if not row.preset:
            row.preset = "gmail"
        super().__init__(row, secrets)
        self._categories: dict[int, str] = {}

    def _gmail_opts(self) -> dict[str, Any]:
        """Gmail import options: All Mail always (it holds every label); Spam and Trash as
        chosen; messages carrying an excluded label are skipped."""
        g = dict(self.cfg.get("gmail") or {})
        legacy = self.cfg.get("folders") or {}
        return {
            "include_spam": g.get("include_spam", True),
            "include_trash": g.get("include_trash", bool(legacy.get("trash", False))),
            "exclude_labels": {x.lower() for x in g.get("exclude_labels") or []},
        }

    async def folder_plan(self) -> list[tuple[str, Target]]:
        opts = self._gmail_opts()
        plan: list[tuple[str, Target]] = []
        for name, flags, _ in await self.folders():
            fl = {f.lower() if isinstance(f, bytes) else str(f).lower().encode() for f in flags}
            if b"\\all" in fl:
                plan.insert(0, (name, ALL_MAIL))
            elif b"\\junk" in fl and opts["include_spam"]:
                plan.append((name, ("role", "junk")))
            elif b"\\trash" in fl and opts["include_trash"]:
                plan.append((name, ("role", "trash")))
        if not plan or plan[0][1] != ALL_MAIL:
            raise RuntimeError("this account has no All Mail folder; enable 'Show in IMAP' for All Mail "
                               "in Gmail settings → Labels")
        return plan

    RECONCILE_VERSION = 1

    async def sync_once(self, ctx) -> dict[str, int]:
        totals = await super().sync_once(ctx)
        if self.row.mode == "import" and ctx.state.get("reconciled") != self.RECONCILE_VERSION \
                and not ctx.stopping.is_set():
            n = await self.reconcile(ctx)
            ctx.state["reconciled"] = self.RECONCILE_VERSION
            await ctx.save_state(ctx.state)
            totals["reconciled"] = n
        return totals

    async def reconcile(self, ctx) -> int:
        """Make every imported message match Gmail: inbox/archive, read/star/important,
        category, labels. Messages that already existed in mailr (from a provider mailbox)
        and messages whose state mailr's models guessed are corrected. Fetches only flags and
        labels, so it is fast even for large mailboxes."""
        s = await self._open()
        changed = 0
        for folder, target in await self.folder_plan():
            sel = await s.select(folder, readonly=True)
            uv = int(sel.get(b"UIDVALIDITY", 0))
            known = sorted(await ctx.known_upstream(folder, uv))
            if not known:
                continue
            if target == ALL_MAIL:
                await self._before_folder(folder, target)
            for i in range(0, len(known), 500):
                if ctx.stopping.is_set():
                    return changed
                chunk = known[i:i + 500]
                data = await s.call(s.client.fetch, chunk, [b"FLAGS", b"X-GM-LABELS"])
                updates: dict[int, dict[str, Any]] = {}
                for uid, d in data.items():
                    k = keywords_from_flags(d.get(b"FLAGS", ()))
                    if target == ALL_MAIL:
                        mapped = map_gmail_labels([decode_label(x) for x in d.get(b"X-GM-LABELS", ()) or ()])
                        roles = mapped["roles"] or ["archive"]
                        k["important"] = mapped["keywords"]["important"] or k.get("important", False)
                        k["flagged"] = mapped["keywords"]["flagged"] or k.get("flagged", False)
                        cat = self._categories.get(int(uid)) or ("primary" if "inbox" in roles else None)
                        updates[int(uid)] = {"roles": roles, "label_names": mapped["label_names"],
                                             "keywords": k, "category": cat}
                    else:
                        updates[int(uid)] = {"roles": [target[1]], "label_names": [], "keywords": k}
                changed += await ctx.apply_provider(folder, uv, updates)
                await ctx.set_status("syncing", f"Matching Gmail state: {folder} {min(i + 500, len(known)):,} of {len(known):,}")
        await ctx.set_status("idle", "Import finished", synced=True)
        return changed

    async def _fetch_items(self) -> list[bytes]:
        return [b"BODY.PEEK[]", b"FLAGS", b"INTERNALDATE", b"RFC822.SIZE", b"X-GM-LABELS", b"X-GM-THRID",
                b"X-GM-MSGID"]

    async def _before_folder(self, folder: str, target: Target) -> None:
        if target != ALL_MAIL:
            return
        s = await self._open()
        cats: dict[int, str] = {}
        for c in CATEGORIES:
            try:
                uids = await s.call(s.client.gmail_search, f"category:{c}")
            except Exception as e:  # noqa: BLE001
                log.info("gmail category search %s failed: %s", c, e)
                continue
            for u in uids:
                cats[int(u)] = c
        self._categories = cats

    def _ingest_kwargs(self, folder: str, target: Target, uidvalidity: int, uid: int, data: dict) -> dict[str, Any] | None:
        flags_kw = keywords_from_flags(data.get(b"FLAGS", ()))
        upstream = {"connector": self.row.id, "folder": folder, "uidvalidity": int(uidvalidity), "uid": int(uid)}
        thrid = data.get(b"X-GM-THRID")
        if thrid:
            upstream["thread_key"] = f"gm:{thrid}"
        if data.get(b"X-GM-MSGID"):
            upstream["gm_msgid"] = str(data[b"X-GM-MSGID"])
        if target != ALL_MAIL:
            kw: dict[str, Any] = {"roles": [target[1]], "label_names": [], "keywords": flags_kw}
        else:
            mapped = map_gmail_labels([decode_label(x) for x in data.get(b"X-GM-LABELS", ()) or ()])
            if mapped["draft"] or flags_kw.get("draft"):
                return None  # drafts are not imported; they belong to the Gmail editor
            excluded = self._gmail_opts()["exclude_labels"]
            if excluded and any(n.lower() in excluded or n.lower().split("/")[0] in excluded
                                for n in mapped["label_names"]):
                return None  # the user chose to leave mail with this label out
            roles = mapped["roles"]
            if "inbox" not in roles and "sent" not in roles:
                roles = roles + ["archive"]
            kw = {"roles": roles, "label_names": mapped["label_names"],
                  "keywords": {**flags_kw, "important": mapped["keywords"]["important"] or flags_kw["important"],
                               "flagged": mapped["keywords"]["flagged"] or flags_kw["flagged"]}}
            cat = self._categories.get(int(uid))
            if cat:
                kw["category"] = cat
            elif "inbox" in roles:
                kw["category"] = "primary"
        kw.update({"received_at": _aware(data.get(b"INTERNALDATE")), "upstream": upstream,
                   "merge_labels": self.row.mode == "mirror",
                   "state_authoritative": self.row.mode == "import",
                   "merge_provider_state": self.row.mode == "import" and getattr(self, "_first_pass", False),
                   "source": "import" if self.row.mode == "import" else "connector",
                   "run_rules": self.row.mode != "import", "notify": self.row.mode != "import"})
        return kw

    async def _pull_remote_changes(self, ctx, folder: str, target: Target, uv: int, fs: dict) -> None:
        if target != ALL_MAIL:
            return await super()._pull_remote_changes(ctx, folder, target, uv, fs)
        s = await self._open()
        last_uid = int(fs.get("last_uid") or 0)
        if not last_uid:
            return
        listing = await self._list_present(s, folder, uv, last_uid)
        if listing is None:
            return
        present, exists, listed = listing
        mods = [f"CHANGEDSINCE {fs['modseq']}"] if s.has("CONDSTORE") and fs.get("modseq") else None
        targets = sorted(present) if mods else sorted(present)[-2000:]
        changed = await s.call(s.client.fetch, targets, [b"FLAGS", b"X-GM-LABELS"], modifiers=mods) if targets else {}
        # The labels this account has (each Gmail label is an IMAP folder). Only these, and the
        # placement roles, follow Gmail; a label that exists only in mailr stays.
        mapped = [n for n, _flags, _d in await self.folders()
                  if n.upper() != "INBOX" and not n.startswith("[Gmail]") and not n.startswith("[Google Mail]")]
        updates = {}
        for u, d in changed.items():
            labels = map_gmail_labels([decode_label(x) for x in d.get(b"X-GM-LABELS", ()) or ()])
            k = keywords_from_flags(d.get(b"FLAGS", ()))
            k["important"] = labels["keywords"]["important"]
            k["flagged"] = k["flagged"] or labels["keywords"]["flagged"]
            roles = labels["roles"] or ["archive"]
            updates[int(u)] = {**k, "label_state": {"roles": roles, "label_names": labels["label_names"],
                                                    "mapped": mapped}}
        if updates:
            await ctx.apply_remote(folder, uv, updates, since=pulled_since(fs))
        known = await ctx.known_upstream(folder, uv)
        gone = await self._guarded_gone(ctx, s, folder, uv, fs, known, present, exists, listed, last_uid)
        if gone:
            # Gone from All Mail = deleted forever or moved to Spam/Trash upstream. All Mail has no
            # label of its own to take away; a message no account holds any more is trashed by
            # finalize_orphans after a full pass.
            await ctx.vanished(folder, uv, gone, ALL_MAIL)

    async def write_back(self, changes: list[WriteBack]) -> list[dict[str, Any]]:
        s = await self._open()
        out = []
        try:
            plan = dict((t, f) for f, t in await self.folder_plan())
            all_mail = plan[ALL_MAIL]
            fast = await self._batch_flags(changes, lambda r: r["folder"] == all_mail)
            if fast is not None:
                return fast
            for ch in changes:
                if "trash" in ch.remove_labels and "trash" not in ch.add_labels and not ch.delete:
                    await _untrash(self, ch, all_mail)
                refs = [r for r in all_refs(ch.upstream) if str(r.get("connector")) == str(self.row.id) and r["folder"] == all_mail]
                if not refs:
                    out.append(ch.upstream)
                    continue
                r = refs[0]
                if not await self._select_ref(r):
                    out.append(ch.upstream)
                    continue
                uid = [r["uid"]]
                if ch.seen is not None:
                    await s.call(s.client.add_flags if ch.seen else s.client.remove_flags, uid, [b"\\Seen"])
                if ch.flagged is not None:
                    await s.call(s.client.add_flags if ch.flagged else s.client.remove_flags, uid, [b"\\Flagged"])
                add = [self._gm_label(x) for x in ch.add_labels if x not in ("trash", "junk", "archive")]
                # Report spam: Gmail keeps a message's labels while it is in Spam and leaves spam out
                # of the label folders itself, so only the Inbox goes (mailr keeps the labels aside;
                # Message.spam_kept_labels). Not spam adds them again, which changes nothing here.
                rem = [self._gm_label(x) for x in ch.remove_labels if x not in ("trash", "junk", "archive")
                       and ("junk" not in ch.add_labels or x == "inbox")]
                if add:
                    await s.call(s.client.add_gmail_labels, uid, [a for a in add if a])
                if rem:
                    await s.call(s.client.remove_gmail_labels, uid, [a for a in rem if a])
                if ch.delete or "trash" in ch.add_labels:
                    await s.call(s.client.copy, uid, plan.get(("role", "trash")) or await _special(self, b"\\trash")
                                 or "[Gmail]/Trash")
                elif "junk" in ch.add_labels:
                    await s.call(s.client.copy, uid, plan.get(("role", "junk")) or await _special(self, b"\\junk")
                                 or "[Gmail]/Spam")
                out.append(refs_to_upstream(refs, keep=ch.upstream))
        finally:
            await self._close()
        return out

    @staticmethod
    def _gm_label(name: str) -> str | None:
        return {"inbox": "\\Inbox", "sent": "\\Sent"}.get(name, None if name in ("drafts",) else name)

    async def test(self) -> TestResult:
        res = await super().test()
        if not res.ok:
            return res
        if not any(f.get("target") == list(ALL_MAIL) for f in res.folders):
            for f in res.folders:
                if f["target"] and f["target"][0] == "role" and f["target"][1] == "all":
                    f["target"] = list(ALL_MAIL)
        # Message counts for the wizard's "skip these labels" and totals (one STATUS each).
        try:
            s = await self._open()
            for f in res.folders:
                name = f["name"]
                system = name.upper() == "INBOX" or name.startswith("[Gmail]") or name.startswith("[Google Mail]")
                f["kind"] = "system" if system else "label"
                if f["kind"] == "label" or f.get("target") in (list(ALL_MAIL), ["role", "junk"], ["role", "trash"]):
                    try:
                        st = await s.call(s.client.folder_status, name, [b"MESSAGES"])
                        f["messages"] = int(st.get(b"MESSAGES", 0))
                    except Exception:  # noqa: BLE001, S110
                        pass
        finally:
            await self._close()
        return res


async def _special(conn: Any, flag: bytes) -> str | None:
    """The Gmail account's (`conn`, a GmailConnector) folder with this special-use flag (\\Trash, \\Junk, \\All): its name differs by
    the account's language ("[Gmail]/Papierkorb", "[Google Mail]/Trash")."""
    for name, flags, _ in await conn.folders():
        if flag in {f.lower() if isinstance(f, bytes) else str(f).lower().encode() for f in flags}:
            return name
    return None


async def _untrash(conn: Any, ch: WriteBack, all_mail: str) -> bool:
    """Restore from Trash. Gmail keeps a trashed message only in its Trash folder (it leaves All
    Mail, so the All Mail ref no longer finds it): it is looked up there by its Gmail id, else
    its Message-ID, and moved back to the Inbox, or to All Mail when it is restored archived.
    False when it is not in Gmail's Trash."""
    trash = await _special(conn, b"\\trash")
    if not trash:
        return False
    s = await conn._open()
    await s.select(trash, readonly=False)
    found: list[int] = []
    gm = (ch.upstream or {}).get("gm_msgid")
    if gm and str(gm).isdigit():
        found = list(await s.call(s.client.search, ["X-GM-MSGID", int(gm)]))
    if not found and ch.message_id:
        found = list(await s.call(s.client.search, ["HEADER", "Message-ID", ch.message_id]))
    if not found:
        return False
    dest = "INBOX" if "inbox" in ch.add_labels else all_mail
    if s.has("MOVE"):
        await s.call(s.client.move, found, dest)
    else:
        await s.call(s.client.copy, found, dest)
        if s.has("UIDPLUS"):  # a plain EXPUNGE would also remove other clients' deleted mail
            await s.call(s.client.add_flags, found, [b"\\Deleted"])
            await s.call(s.client.uid_expunge, found)
    conn._selected_uv = None  # the next write-back selects All Mail again
    return True
