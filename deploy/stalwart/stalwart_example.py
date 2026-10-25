"""Minimal Stalwart v0.16 client for mailr (requests only). Verified against v0.16.23.

Auth model: one service account with the Admin role (created by provision.json).
Admin can address ANY account's mail by passing that account's accountId in
JMAP calls, and is not subject to per-user maxConcurrentRequests. The JMAP
accountId of a user equals the id returned by x:Account/set.

Env: STALWART_URL (default http://stalwart:8080), STALWART_SVC_USER, STALWART_SVC_PASSWORD
"""
import os
import sys
import requests

BASE = os.environ.get("STALWART_URL", "http://stalwart:8080").rstrip("/")
SVC = (os.environ.get("STALWART_SVC_USER", "mailr-svc@mailr.test"),
       os.environ.get("STALWART_SVC_PASSWORD", "SvcPw-2026-abc"))
MAIL = ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"]
MGMT = ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"]

s = requests.Session()
s.auth = SVC


def jmap(using, calls):
    r = s.post(f"{BASE}/jmap/", json={"using": using, "methodCalls": calls}, timeout=60)
    r.raise_for_status()
    out = r.json()["methodResponses"]
    for name, body, _ in out:
        if name == "error":
            raise RuntimeError(body)
    return out


def domain_id(domain):
    ids = jmap(MGMT, [["x:Domain/query", {"filter": {"name": domain}}, "q"]])[0][1]["ids"]
    if not ids:
        raise LookupError(domain)
    return ids[0]


def create_account(local, domain, password, quota_bytes=None, description=None):
    acct = {"@type": "User", "name": local, "domainId": domain_id(domain),
            "credentials": {"0": {"@type": "Password", "secret": password}}}
    if quota_bytes:
        acct["quotas"] = {"maxDiskQuota": quota_bytes}
    if description:
        acct["description"] = description
    res = jmap(MGMT, [["x:Account/set", {"create": {"a": acct}}, "c"]])[0][1]
    if "a" not in res.get("created", {}):
        raise RuntimeError(res.get("notCreated"))
    return res["created"]["a"]["id"]          # == JMAP accountId


def set_password(account_id, password):
    res = jmap(MGMT, [["x:Account/set", {"update": {account_id: {"credentials/0/secret": password}}}, "u"]])[0][1]
    if account_id not in res.get("updated", {}):
        raise RuntimeError(res.get("notUpdated"))


def delete_account(account_id):
    # Community edition: destroys the account's mail immediately (no retention).
    res = jmap(MGMT, [["x:Account/set", {"destroy": [account_id]}, "d"]])[0][1]
    if account_id not in res.get("destroyed", []):
        raise RuntimeError(res.get("notDestroyed"))


def mailbox_ids_by_role(account_id):
    lst = jmap(MAIL, [["Mailbox/get", {"accountId": account_id, "properties": ["name", "role"]}, "m"]])[0][1]["list"]
    return {m["role"]: m["id"] for m in lst if m["role"]}


def import_message(account_id, raw: bytes, mailbox_ids, keywords=None, received_at=None):
    up = s.post(f"{BASE}/jmap/upload/{account_id}/", data=raw,
                headers={"Content-Type": "message/rfc822"}, timeout=120)
    up.raise_for_status()
    email = {"blobId": up.json()["blobId"], "mailboxIds": {m: True for m in mailbox_ids},
             "keywords": keywords or {}}
    if received_at:
        email["receivedAt"] = received_at     # RFC 3339 UTC, e.g. "2026-09-21T10:00:05Z"
    res = jmap(MAIL, [["Email/import", {"accountId": account_id, "emails": {"e": email}}, "i"]])[0][1]
    if "e" not in res.get("created", {}):
        raise RuntimeError(res.get("notCreated"))
    return res["created"]["e"]                # {"id", "threadId", "blobId", "size"}


def query_threads(account_id, mailbox_id=None, text=None, limit=50):
    flt = {}
    if mailbox_id:
        flt["inMailbox"] = mailbox_id
    if text:
        flt["text"] = text
    out = jmap(MAIL, [
        ["Email/query", {"accountId": account_id, "filter": flt or None,
                         "sort": [{"property": "receivedAt", "isAscending": False}],
                         "collapseThreads": True, "limit": limit, "calculateTotal": True}, "q"],
        ["Email/get", {"accountId": account_id,
                       "#ids": {"resultOf": "q", "name": "Email/query", "path": "/ids"},
                       "properties": ["threadId", "subject", "from", "receivedAt", "keywords", "preview"]}, "g"],
        ["Thread/get", {"accountId": account_id,
                        "#ids": {"resultOf": "g", "name": "Email/get", "path": "/list/*/threadId"}}, "t"],
    ])
    emails = out[1][1]["list"]
    threads = {t["id"]: t["emailIds"] for t in out[2][1]["list"]}
    return [{"threadId": e["threadId"], "subject": e["subject"], "from": e["from"],
             "receivedAt": e["receivedAt"], "unread": "$seen" not in e["keywords"],
             "count": len(threads[e["threadId"]]), "latestEmailId": e["id"]} for e in emails]


if __name__ == "__main__":
    domain = sys.argv[1] if len(sys.argv) > 1 else "mailr.test"
    aid = create_account("demo", domain, "DemoPass-123", quota_bytes=1 << 30)
    print("created account", aid)
    roles = mailbox_ids_by_role(aid)
    raw1 = (b"From: Bob <bob@example.com>\r\nTo: demo@" + domain.encode() + b"\r\n"
            b"Subject: Budget\r\nMessage-ID: <b1@example.com>\r\nDate: Mon, 21 Sep 2026 10:00:00 +0000\r\n\r\n"
            b"Numbers attached.\r\n")
    raw2 = (b"From: Bob <bob@example.com>\r\nTo: demo@" + domain.encode() + b"\r\n"
            b"Subject: Re: Budget\r\nMessage-ID: <b2@example.com>\r\nIn-Reply-To: <b1@example.com>\r\n"
            b"References: <b1@example.com>\r\nDate: Mon, 21 Sep 2026 12:00:00 +0000\r\n\r\nUpdated numbers.\r\n")
    print(import_message(aid, raw1, [roles["inbox"]], {"$seen": True}, "2026-09-21T10:00:05Z"))
    print(import_message(aid, raw2, [roles["inbox"]], {}, "2026-09-21T12:00:05Z"))
    for t in query_threads(aid, roles["inbox"]):
        print(t)
    print("search:", query_threads(aid, text="numbers"))
    set_password(aid, "DemoPass-456")
    delete_account(aid)
    print("deleted", aid)
