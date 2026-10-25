"""Instance-level secrets are bound to the row and field they are stored in:
model endpoint API keys, the image proxy's egress URL and the push (VAPID) key. A value sealed for
one place does not open in another; values stored before contexts existed still open and are
rewritten under their context at startup (upgrade_ciphertexts)."""

import asyncio
import base64

import pytest
from sqlalchemy import delete

from mailr.core import crypto
from mailr.db import sessionmaker
from mailr.models import InstanceSetting, ModelEndpoint


def test_instance_value_opens_only_under_its_context():
    a = crypto.encrypt_instance("sk-one", context=crypto.model_endpoint_context(1))
    assert not crypto.is_legacy(a)
    assert crypto.decrypt_instance(a, context=crypto.model_endpoint_context(1)) == "sk-one"
    for other in (crypto.model_endpoint_context(2), crypto.EGRESS_CONTEXT, crypto.VAPID_CONTEXT):
        with pytest.raises(ValueError):
            crypto.decrypt_instance(a, context=other)
    # Rewriting the stored context byte does not help: the context is in the AAD.
    ctx2 = crypto.model_endpoint_context(2).encode()
    forged = a[:1] + bytes([len(ctx2)]) + ctx2 + a[2 + a[1]:]
    with pytest.raises(Exception):  # noqa: B017 - InvalidTag
        crypto.decrypt_instance(forged, context=crypto.model_endpoint_context(2))


def test_legacy_instance_value_opens_only_without_a_context_or_with_the_flag(monkeypatch):
    from mailr.config import get_settings

    old = crypto.encrypt_instance("sk-old")  # as written before contexts existed
    assert crypto.is_legacy(old)
    assert crypto.decrypt_instance(old) == "sk-old"  # the startup upgrade reads it this way
    # Where a context is required, an unbound value is refused ...
    with pytest.raises(ValueError):
        crypto.decrypt_instance(old, context=crypto.model_endpoint_context(5))
    # ... unless MAILR_CRYPTO_LEGACY_READS is set.
    monkeypatch.setattr(get_settings(), "crypto_legacy_reads", True)
    assert crypto.decrypt_instance(old, context=crypto.model_endpoint_context(5)) == "sk-old"


def test_egress_and_vapid_are_sealed_for_their_setting():
    from mailr.core import imageproxy, webpush

    sealed = imageproxy.seal_egress("http://u:pw@egress:8888")
    blob = base64.b64decode(sealed[4:])
    assert not crypto.is_legacy(blob)
    with pytest.raises(ValueError):
        crypto.decrypt_instance(blob, context=crypto.VAPID_CONTEXT)
    pair = webpush._new_pair()
    with pytest.raises(ValueError):
        crypto.decrypt_instance(base64.b64decode(pair["private"]), context=crypto.EGRESS_CONTEXT)
    webpush._private_key(pair)  # opens under its own context
    # An egress value moved into another setting's place reads as unreadable (fails closed).
    moved = "enc:" + pair["private"]
    assert imageproxy.open_egress(moved).endswith(".invalid:1")


@pytest.fixture
async def _clean(app):
    async def wipe():
        async with sessionmaker()() as db:
            await db.execute(delete(ModelEndpoint))
            await db.execute(delete(InstanceSetting).where(InstanceSetting.key.in_(["image_proxy_egress",
                                                                                    "webpush_vapid"])))
            await db.commit()
    await wipe()
    yield
    await wipe()


async def test_endpoint_key_is_bound_to_its_row(admin_client, _clean, monkeypatch):
    from mailr.api import admin_routes
    from mailr.intel import llm

    async def ok(url):
        return url.rstrip("/")

    monkeypatch.setattr(admin_routes, "_safe_base", ok)
    monkeypatch.setattr(admin_routes, "_check_local", lambda *_a: asyncio.sleep(0))  # names that do not resolve
    ids = []
    for name in ("one", "two"):
        r = await admin_client.post("/api/v1/admin/models", json={
            "name": name, "base_url": f"http://{name}.example.com/v1", "locality": "local",
            "models": ["m"], "api_key": f"sk-{name}"})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    async with sessionmaker()() as db:
        one, two = await db.get(ModelEndpoint, ids[0]), await db.get(ModelEndpoint, ids[1])
        assert not crypto.is_legacy(one.api_key_enc)
        assert llm.endpoint_key(one) == "sk-one" and llm.endpoint_key(two) == "sk-two"
        # Copying one endpoint's stored key onto another row does not make it usable there.
        two.api_key_enc = one.api_key_enc
        with pytest.raises(ValueError):
            llm.endpoint_key(two)
    # A new key entered on edit is sealed for that row too.
    r = await admin_client.patch(f"/api/v1/admin/models/{ids[1]}", json={
        "name": "two", "base_url": "http://two.example.com/v1", "locality": "local", "models": ["m"],
        "api_key": "sk-two-b"})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        assert llm.endpoint_key(await db.get(ModelEndpoint, ids[1])) == "sk-two-b"


async def test_legacy_instance_secrets_are_upgraded(_clean):
    from mailr.core import imageproxy, webpush
    from mailr.intel import llm

    async with sessionmaker()() as db:
        ep = ModelEndpoint(name="old", base_url="http://old.example.com/v1", locality="local",
                           models=["m"], defaults={})
        db.add(ep)
        await db.flush()
        ep.api_key_enc = crypto.encrypt_instance("sk-legacy")
        pair = webpush._new_pair()
        pair["private"] = base64.b64encode(crypto.encrypt_instance(
            crypto.decrypt_instance(base64.b64decode(pair["private"])))).decode()
        db.add(InstanceSetting(key="webpush_vapid", value=pair))
        db.add(InstanceSetting(key="image_proxy_egress",
                               value="enc:" + base64.b64encode(crypto.encrypt_instance("http://u:pw@egress:8888")).decode()))
        await db.commit()
        eid = ep.id
    # Legacy values are refused where a context is required until the upgrade.
    async with sessionmaker()() as db:
        with pytest.raises(ValueError):
            llm.endpoint_key(await db.get(ModelEndpoint, eid))

    async with sessionmaker()() as db:
        assert await crypto.upgrade_ciphertexts(db) == 3
    async with sessionmaker()() as db:
        assert await crypto.upgrade_ciphertexts(db) == 0  # idempotent
        ep = await db.get(ModelEndpoint, eid)
        assert not crypto.is_legacy(ep.api_key_enc) and llm.endpoint_key(ep) == "sk-legacy"
        with pytest.raises(ValueError):
            crypto.decrypt_instance(ep.api_key_enc, context=crypto.model_endpoint_context(eid + 1))
        egress = (await db.get(InstanceSetting, "image_proxy_egress")).value
        assert not crypto.is_legacy(base64.b64decode(egress[4:]))
        assert imageproxy.open_egress(egress) == "http://u:pw@egress:8888"
        stored = (await db.get(InstanceSetting, "webpush_vapid")).value
        assert stored["public"] == pair["public"]
        assert not crypto.is_legacy(base64.b64decode(stored["private"]))
        webpush._private_key(stored)
