from mailr.connectors.gmail import ALL_MAIL, GmailConnector
from mailr.models import Connector


def _conn(gmail: dict) -> GmailConnector:
    row = Connector(id=9, user_id=1, kind="gmail", name="g", mode="mirror",
                    config={"email": "x@gmail.com", "imap": {"host": "imap.gmail.com"}, "gmail": gmail})
    return GmailConnector(row, {"password": "x"})


def _data(labels: list[bytes]) -> dict:
    return {b"FLAGS": (b"\\Seen",), b"X-GM-LABELS": tuple(labels), b"X-GM-THRID": 1, b"INTERNALDATE": None}


def test_excluded_labels_are_skipped():
    c = _conn({"exclude_labels": ["SMS", "Call log"]})
    assert c._ingest_kwargs("[Gmail]/All Mail", ALL_MAIL, 1, 5, _data([b"SMS"])) is None
    assert c._ingest_kwargs("[Gmail]/All Mail", ALL_MAIL, 1, 6, _data([b'"Call log"'])) is None
    kept = c._ingest_kwargs("[Gmail]/All Mail", ALL_MAIL, 1, 7, _data([b"\\Inbox", b"eBay"]))
    assert kept is not None and kept["label_names"] == ["eBay"] and "inbox" in kept["roles"]


def test_spam_and_trash_options():
    assert _conn({})._gmail_opts()["include_spam"] is True
    assert _conn({})._gmail_opts()["include_trash"] is False
    assert _conn({"include_spam": False, "include_trash": True})._gmail_opts() == {
        "include_spam": False, "include_trash": True, "exclude_labels": set()}


def test_idle_watches_all_mail_on_gmail_and_the_inbox_elsewhere():
    """Gmail's plan has no inbox folder (it is synced from All Mail), so IDLE never ran and new mail
    waited for the next full pass, 3 to 6 minutes (28 Oct 2026). IDLE watches All Mail there."""
    from mailr.connectors.imap import ImapConnector

    plan = {"[Gmail]/All Mail": ALL_MAIL, "[Gmail]/Spam": ("role", "junk")}
    assert _conn({})._push_folder(plan) == "[Gmail]/All Mail"
    row = Connector(id=8, user_id=1, kind="imap", name="i", mode="mirror",
                    config={"email": "x@example.com", "imap": {"host": "imap.example.com"}})
    plain = ImapConnector(row, {"password": "x"})
    assert plain._push_folder({"INBOX": ("role", "inbox"), "Sent": ("role", "sent")}) == "INBOX"
    assert plain._push_folder({"Archive": ("label", "Archive")}) is None


def test_the_idle_loop_takes_its_folder_from_the_connector():
    import inspect

    from mailr.connectors.imap import ImapConnector

    assert "self._push_folder(plan)" in inspect.getsource(ImapConnector._idle_loop)
