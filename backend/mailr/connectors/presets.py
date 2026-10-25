"""Known mail providers: server settings and quirks.

`tls` is "ssl" (implicit TLS), "starttls", or "none". Presets only prefill; every field
can be overridden in the connector config.
"""

from __future__ import annotations

from typing import Any

PRESETS: dict[str, dict[str, Any]] = {
    "fastmail": {
        "name": "Fastmail",
        "domains": ["fastmail.com", "fastmail.fm", "messagingengine.com"],
        "mx": ["messagingengine.com"],
        "imap": {"host": "imap.fastmail.com", "port": 993, "tls": "ssl"},
        "smtp": {"host": "smtp.fastmail.com", "port": 465, "tls": "ssl"},
        "auth": ["app_password"],
        "folders": {"sent": "Sent", "junk": "Spam", "trash": "Trash", "drafts": "Drafts", "archive": "Archive"},
        "idle": True,
        "max_connections": 10,
        "notes": "Fastmail requires an app password with IMAP and SMTP access.",
    },
    "migadu": {
        "name": "Migadu",
        "domains": [],
        "mx": ["migadu.com"],
        "imap": {"host": "imap.migadu.com", "port": 993, "tls": "ssl"},
        "smtp": {"host": "smtp.migadu.com", "port": 465, "tls": "ssl"},
        "auth": ["password"],
        "folders": {"sent": "Sent", "junk": "Junk", "trash": "Trash", "drafts": "Drafts", "archive": "Archive"},
        "idle": True,
        "max_connections": 10,
    },
    "mailbox.org": {
        "name": "mailbox.org",
        "domains": ["mailbox.org"],
        "mx": ["mailbox.org"],
        "imap": {"host": "imap.mailbox.org", "port": 993, "tls": "ssl"},
        "smtp": {"host": "smtp.mailbox.org", "port": 465, "tls": "ssl"},
        "auth": ["password", "app_password"],
        "folders": {"sent": "Sent", "junk": "Junk", "trash": "Trash", "drafts": "Drafts", "archive": "Archive"},
        "idle": True,
        "max_connections": 10,
    },
    "protonmail-bridge": {
        "name": "Proton Mail (Bridge)",
        "domains": ["proton.me", "protonmail.com", "pm.me"],
        "mx": ["protonmail.ch"],
        "imap": {"host": "127.0.0.1", "port": 1143, "tls": "starttls"},
        "smtp": {"host": "127.0.0.1", "port": 1025, "tls": "starttls"},
        "auth": ["bridge_password"],
        "folders": {"sent": "Sent", "junk": "Spam", "trash": "Trash", "drafts": "Drafts", "archive": "Archive"},
        "idle": True,
        "max_connections": 4,
        "tls_verify": False,
        "notes": "Proton needs Proton Mail Bridge running where Mailr can reach it; use the "
                 "Bridge password. Bridge uses a self-signed certificate.",
    },
    "icloud": {
        "name": "iCloud Mail",
        "domains": ["icloud.com", "me.com", "mac.com"],
        "mx": ["mail.icloud.com"],
        "imap": {"host": "imap.mail.me.com", "port": 993, "tls": "ssl"},
        "smtp": {"host": "smtp.mail.me.com", "port": 587, "tls": "starttls"},
        "auth": ["app_password"],
        "folders": {"sent": "Sent Messages", "junk": "Junk", "trash": "Deleted Messages",
                    "drafts": "Drafts", "archive": "Archive"},
        "idle": True,
        "max_connections": 4,
        "notes": "Create an app-specific password at appleid.apple.com. The login is the part "
                 "before @ for some older accounts.",
    },
    "yahoo": {
        "name": "Yahoo Mail",
        "domains": ["yahoo.com", "ymail.com", "rocketmail.com"],
        "mx": ["yahoodns.net"],
        "imap": {"host": "imap.mail.yahoo.com", "port": 993, "tls": "ssl"},
        "smtp": {"host": "smtp.mail.yahoo.com", "port": 465, "tls": "ssl"},
        "auth": ["app_password"],
        "folders": {"sent": "Sent", "junk": "Bulk", "trash": "Trash", "drafts": "Draft", "archive": "Archive"},
        "idle": True,
        "max_connections": 5,
    },
    "gmail": {
        "name": "Gmail",
        "kind": "gmail",
        "domains": ["gmail.com", "googlemail.com"],
        "mx": ["google.com", "googlemail.com"],
        "imap": {"host": "imap.gmail.com", "port": 993, "tls": "ssl"},
        "smtp": {"host": "smtp.gmail.com", "port": 465, "tls": "ssl"},
        "auth": ["app_password"],
        "folders": {"sent": "[Gmail]/Sent Mail", "junk": "[Gmail]/Spam", "trash": "[Gmail]/Trash",
                    "drafts": "[Gmail]/Drafts", "all": "[Gmail]/All Mail"},
        "idle": True,
        "max_connections": 15,
        "notes": "Use an app password (Google Account → Security → App passwords; needs 2-step "
                 "verification). Mailr imports All Mail once with labels and threads.",
    },
    "office365": {
        "name": "Microsoft 365 / Outlook.com",
        "domains": ["outlook.com", "hotmail.com", "live.com", "msn.com"],
        "mx": ["protection.outlook.com", "olc.protection.outlook.com"],
        "imap": {"host": "outlook.office365.com", "port": 993, "tls": "ssl"},
        "smtp": {"host": "smtp.office365.com", "port": 587, "tls": "starttls"},
        "auth": ["oauth2"],
        "folders": {"sent": "Sent Items", "junk": "Junk Email", "trash": "Deleted Items",
                    "drafts": "Drafts", "archive": "Archive"},
        "idle": True,
        "max_connections": 8,
        "supported": False,
        "notes": "Microsoft 365 requires OAuth sign-in, which the Microsoft 365 connector will "
                 "provide. Password sign-in is blocked by Microsoft.",
    },
    "zoho": {
        "name": "Zoho Mail",
        "domains": ["zoho.com", "zohomail.com"],
        "mx": ["zoho.com", "zoho.eu"],
        "imap": {"host": "imap.zoho.com", "port": 993, "tls": "ssl"},
        "smtp": {"host": "smtp.zoho.com", "port": 465, "tls": "ssl"},
        "auth": ["password", "app_password"],
        "folders": {"sent": "Sent", "junk": "Spam", "trash": "Trash", "drafts": "Drafts", "archive": "Archive"},
        "idle": True,
        "max_connections": 5,
        "notes": "IMAP access must be enabled in Zoho Mail settings. EU accounts use imap.zoho.eu.",
    },
    "purelymail": {
        "name": "Purelymail",
        "domains": ["purelymail.com"],
        "mx": ["purelymail.com"],
        "imap": {"host": "imap.purelymail.com", "port": 993, "tls": "ssl"},
        "smtp": {"host": "smtp.purelymail.com", "port": 465, "tls": "ssl"},
        "smtp_alt": {"host": "smtp.purelymail.com", "port": 587, "tls": "starttls"},
        "auth": ["password", "app_password"],
        "folders": {"sent": "Sent", "junk": "Junk", "trash": "Trash", "drafts": "Drafts", "archive": "Archive"},
        "idle": True,
        "max_connections": 10,
        "notes": "Use an app password (Account → Security). Routing rules that forward to another "
                 "address leave the Purelymail mailbox empty: remove them before choosing "
                 "“Mailr keeps the mail”.",
    },
    "posteo": {
        "name": "Posteo",
        "domains": ["posteo.de", "posteo.net", "posteo.org"],
        "mx": ["posteo.de"],
        "imap": {"host": "posteo.de", "port": 993, "tls": "ssl"},
        "smtp": {"host": "posteo.de", "port": 465, "tls": "ssl"},
        "auth": ["password"],
        "folders": {"sent": "Sent", "junk": "Spam", "trash": "Trash", "drafts": "Drafts", "archive": "Archive"},
        "idle": True,
        "max_connections": 8,
    },
    "generic": {
        "name": "Other IMAP/SMTP",
        "domains": [],
        "mx": [],
        "imap": {"host": "", "port": 993, "tls": "ssl"},
        "smtp": {"host": "", "port": 465, "tls": "ssl"},
        "auth": ["password"],
        "folders": {},
        "idle": True,
        "max_connections": 5,
    },
}


def public_presets() -> list[dict[str, Any]]:
    out = []
    for pid, p in PRESETS.items():
        out.append({"id": pid, "name": p["name"], "kind": p.get("kind", "imap"), "imap": p["imap"],
                    "smtp": p["smtp"], "auth": p["auth"], "supported": p.get("supported", True),
                    "notes": p.get("notes", ""), "tls_verify": p.get("tls_verify", True)})
    return out


def preset_for_domain(domain: str) -> str | None:
    d = domain.lower()
    for pid, p in PRESETS.items():
        if d in p["domains"]:
            return pid
    return None


def preset_for_mx(hosts: list[str]) -> str | None:
    for h in hosts:
        h = h.lower().rstrip(".")
        for pid, p in PRESETS.items():
            for suffix in p["mx"]:
                if h == suffix or h.endswith("." + suffix):
                    return pid
    return None
