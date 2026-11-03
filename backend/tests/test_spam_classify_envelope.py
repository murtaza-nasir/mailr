"""ClassifySpam's envelope fields follow the store's Stalwart version: envFrom/envRcptTo on 0.16,
mailFrom/rcptTo on 1.x (docs/design/stalwart-1.0-migration.md, section c). A 1.x server refuses the
old names with invalidPatch. Runs against a fake management API, no database."""

from types import SimpleNamespace

import pytest

from mailr.core import store_groupware as sg
from mailr.intel import spam

RAW = (b"Received: from mail-sor-f41.google.com (mail-sor-f41.google.com [209.85.220.41])\r\n"
       b"\tby mx.example.net with ESMTPS id x; Mon, 2 Nov 2026 10:00:00 +0000\r\n"
       b"From: bob@gmail.com\r\nTo: alice@mailr.internal\r\nSubject: hi\r\n\r\nhello\r\n")


class FakeStore:
    def __init__(self, http: dict):
        self.base_url = f"http://fake-{id(self)}"
        self.http = http
        self.sent: list[dict] = []

    async def call(self, calls, using=None):
        return [[m, {"list": [{"id": "singleton", **self.http}]}, t] for m, _a, t in calls]

    async def one(self, method, args, using=None):
        self.sent.append(args["create"]["c"])
        return {"created": {"c": {"score": 1.0, "result": "ham", "tags": {}}}}


@pytest.mark.parametrize(("http", "names"), [
    ({"useXForwarded": False}, ("envFrom", "envRcptTo")),
    ({"clientIpSource": "peer", "forwardedHops": 1}, ("mailFrom", "rcptTo")),
])
async def test_envelope_names_follow_the_version(monkeypatch, http, names):
    store = FakeStore(http)
    monkeypatch.setattr(spam, "service_client", lambda: store)
    m = SimpleNamespace(from_email="bob@gmail.com", delivered_to=["alice@mailr.internal"], to=[])
    assert await spam.classify(None, m, RAW) == {"score": 1.0, "result": "ham", "tags": {}}  # type: ignore[arg-type]
    req = store.sent[0]
    assert req[names[0]] == "bob@gmail.com" and req[names[1]] == {"alice@mailr.internal": True}
    assert not {"envFrom", "envRcptTo", "mailFrom", "rcptTo"} - set(names) & set(req)
    assert req["remoteIp"] == "209.85.220.41"
    sg.forget_version()
