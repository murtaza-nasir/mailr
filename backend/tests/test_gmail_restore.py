"""Gmail mirror: Restore from Trash. A trashed Gmail message is only in Gmail's Trash folder (it
leaves All Mail), so the write-back finds it there by its Gmail id, else its Message-ID, and moves
it back; folders are found by their special-use flags, whatever the account's language."""

from types import SimpleNamespace

from mailr.connectors.base import WriteBack
from mailr.connectors.gmail import ALL_MAIL, GmailConnector

FOLDERS = [("INBOX", (b"\\HasNoChildren",), "/"),
           ("[Google Mail]/Alle Nachrichten", (b"\\All", b"\\HasNoChildren"), "/"),
           ("[Google Mail]/Papierkorb", (b"\\Trash", b"\\HasNoChildren"), "/"),
           ("[Google Mail]/Spam", (b"\\Junk",), "/")]


def _conn(monkeypatch, *, caps=("MOVE", "UIDPLUS"), hits=None):
    calls: list[tuple[str, tuple]] = []
    hits = {} if hits is None else hits

    class FakeClient:
        def search(self, criteria):
            calls.append(("search", (criteria,)))
            return hits.get(criteria[0], [])

        def __getattr__(self, name):
            return lambda *a: calls.append((name, a))

    class FakeSession:
        client = FakeClient()

        async def call(self, fn, *a, **kw):
            return fn(*a, **kw)

        async def select(self, folder, readonly=True):
            calls.append(("select", (folder,)))
            return {b"UIDVALIDITY": 1}

        def has(self, cap):
            return cap in caps

    conn = GmailConnector.__new__(GmailConnector)
    conn.row = SimpleNamespace(id=7, preset="gmail", config={"email": "a@gmail.example"}, mode="mirror", kind="gmail")
    session = FakeSession()

    async def _open():
        return session

    async def _close(hard=False):
        return None

    async def folders():
        return FOLDERS

    async def folder_plan():
        return [("[Google Mail]/Alle Nachrichten", ALL_MAIL)]

    async def _select_ref(r):
        return True

    for name, fn in (("_open", _open), ("_close", _close), ("folders", folders), ("folder_plan", folder_plan),
                     ("_select_ref", _select_ref)):
        monkeypatch.setattr(conn, name, fn, raising=False)
    return conn, calls


async def test_restore_moves_the_message_out_of_gmails_trash(monkeypatch):
    conn, calls = _conn(monkeypatch, hits={"X-GM-MSGID": [91]})
    up = {"connector": 7, "folder": "[Google Mail]/Alle Nachrichten", "uidvalidity": 1, "uid": 42,
          "gm_msgid": "1781234567890123456"}
    await conn.write_back([WriteBack(upstream=up, add_labels=["inbox"], remove_labels=["trash"],
                                     message_id="<a1@example.org>")])
    assert ("select", ("[Google Mail]/Papierkorb",)) in calls
    assert ("search", (["X-GM-MSGID", 1781234567890123456],)) in calls
    assert ("move", ([91], "INBOX")) in calls


async def test_restore_archived_and_by_message_id_without_move(monkeypatch):
    conn, calls = _conn(monkeypatch, caps=("UIDPLUS",), hits={"HEADER": [12]})
    up = {"removed": True}  # the All Mail ref went when the message left All Mail
    out = await conn.write_back([WriteBack(upstream=up, add_labels=["Clients"], remove_labels=["trash"],
                                           message_id="<b2@example.org>")])
    assert ("search", (["HEADER", "Message-ID", "<b2@example.org>"],)) in calls
    assert ("copy", ([12], "[Google Mail]/Alle Nachrichten")) in calls
    assert ("add_flags", ([12], [b"\\Deleted"])) in calls and ("uid_expunge", ([12],)) in calls
    assert out == [up]


async def test_trash_and_spam_copies_find_localized_folders(monkeypatch):
    conn, calls = _conn(monkeypatch)
    up = {"connector": 7, "folder": "[Google Mail]/Alle Nachrichten", "uidvalidity": 1, "uid": 42}
    await conn.write_back([WriteBack(upstream=up, add_labels=["trash"], remove_labels=["inbox"])])
    assert ("copy", ([42], "[Google Mail]/Papierkorb")) in calls
    assert not any(n == "move" for n, _ in calls)  # moving to Trash is not a restore
    calls.clear()
    await conn.write_back([WriteBack(upstream=up, add_labels=["junk"], remove_labels=["inbox"])])
    assert ("copy", ([42], "[Google Mail]/Spam")) in calls


async def test_a_message_not_in_trash_is_left_alone(monkeypatch):
    conn, calls = _conn(monkeypatch)
    up = {"connector": 7, "folder": "[Google Mail]/Alle Nachrichten", "uidvalidity": 1, "uid": 42, "gm_msgid": "5"}
    await conn.write_back([WriteBack(upstream=up, add_labels=["inbox"], remove_labels=["trash"])])
    assert not any(n in ("move", "copy", "uid_expunge") for n, _ in calls)
    assert ("add_gmail_labels", ([42], ["\\Inbox"])) in calls
