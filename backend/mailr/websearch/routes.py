"""Admin → Web search: the search providers and the page readers (site/docs/admin/web-search.md).

Both are rows of web_search_providers; `purpose` ("search" or "reader") says which list a row is
in, and its kind comes from that list's registry (adapters.ADAPTERS or readers.READERS). A row
keeps its purpose: an edit cannot move it to the other list.

Admins only. Every change is audited (as the other admin routes). The API key is write-only: it is
sealed for its row, never returned (`has_api_key` says whether one is stored), and cleared when the
base URL changes without a new key, so a stored key only ever goes to the URL it was saved for.

The server's own page reader (`from_env`, MAILR_BROWSER_URL) is listed like the others; an edit
changes only its route, order, limits and whether it is on, and it cannot be deleted. Its token
stays in the environment. A browserless reader's route is in the audit row (`route`).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.api.admin_routes import _audit_admin_change
from mailr.core.audit import audit
from mailr.core.auth import Principal, admin
from mailr.core.crypto import encrypt_instance, web_search_provider_context
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import WebSearchProvider
from mailr.websearch import readers, service
from mailr.websearch.adapters import ADAPTERS

router = APIRouter(prefix="/admin/websearch", tags=["admin"], dependencies=[Depends(_audit_admin_change)])


def _kinds(purpose: str) -> dict[str, Any]:
    return readers.READERS if purpose == "reader" else ADAPTERS


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    purpose: str = Field("search", pattern="^(search|reader)$")
    kind: str
    base_url: str = ""
    # Omitted (None): keep the stored key. "": remove it.
    api_key: str | None = None
    network: str = Field("internet", pattern="^(internet|local)$")
    use_egress: bool = True
    priority: int = Field(100, ge=0, le=10000)
    enabled: bool = True
    max_results: int = Field(10, ge=1, le=20)
    timeout_s: int = Field(10, ge=1, le=60)
    per_minute: int = Field(0, ge=0, le=10000)
    per_day: int = Field(0, ge=0, le=1000000)


TEST_PAGE = "https://en.wikipedia.org/wiki/Email"


class TestIn(BaseModel):
    query: str = Field("open source mail server", max_length=200)
    # A reader's test reads this page.
    url: str = Field(TEST_PAGE, max_length=2000)
    purpose: str | None = Field(None, pattern="^(search|reader)$")
    # A draft (the dialog before Save): these fields; with `id`, the stored key is used when no key
    # is given and the URL is unchanged.
    id: int | None = None
    kind: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    network: str | None = Field(None, pattern="^(internet|local)$")
    use_egress: bool | None = None
    timeout_s: int | None = Field(None, ge=1, le=60)


def provider_out(p: WebSearchProvider) -> dict[str, Any]:
    today = datetime.now(UTC).date().isoformat()
    return {"id": p.id, "name": p.name, "purpose": p.purpose or "search", "kind": p.kind, "base_url": p.base_url,
            "has_api_key": bool(p.api_key_enc) or (bool(p.from_env) and bool(service.key_of(p))),
            "from_env": bool(p.from_env),
            "network": p.network, "use_egress": p.use_egress, "priority": p.priority, "enabled": p.enabled,
            "max_results": p.max_results, "timeout_s": p.timeout_s, "per_minute": p.per_minute,
            "per_day": p.per_day, "used_today": p.usage_count if p.usage_day == today else 0,
            "last_ok_at": p.last_ok_at.isoformat() if p.last_ok_at else None, "last_error": p.last_error or None}


async def _checked(body: ProviderIn | TestIn, kind: str, network: str, purpose: str = "search") -> str:
    kinds = _kinds(purpose)
    try:
        base = service.check_base(kind, network, body.base_url or kinds[kind].default_url
                                  if kind in kinds else body.base_url or "", kinds)
        await service.check_target(network, base)
    except service.ProviderError as e:
        raise HTTPException(400, str(e)) from None
    return base


@router.get("")
async def overview(p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """The providers and the page readers in the order they are tried, the kinds mailr knows, and
    how queries and page addresses leave."""
    from mailr.config import get_settings
    from mailr.core import egress, netguard

    configured = bool(await egress.configured(db))
    return {"providers": [provider_out(x) for x in await service.providers(db, enabled_only=False)],
            "readers": [provider_out(x) for x in await service.providers(db, enabled_only=False, purpose="reader")],
            "kinds": [a.meta() for a in ADAPTERS.values()],
            "reader_kinds": [r.meta() for r in readers.READERS.values()],
            "browser_proxy_set": bool((get_settings().browser_egress_proxy or "").strip()),
            "egress": {"configured": configured, "required": egress.required()},
            "private_hosts": netguard.private_mode()}


@router.post("/providers")
async def add_provider(body: ProviderIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    if body.kind not in _kinds(body.purpose):
        raise HTTPException(400, f"unknown kind {body.kind!r}")  # i18n: internal
    base = await _checked(body, body.kind, body.network, body.purpose)
    if (await db.execute(select(WebSearchProvider.id).where(WebSearchProvider.name == body.name.strip()))).first():
        raise ApiError(409, "websearch.provider_name_taken")
    row = WebSearchProvider(name=body.name.strip(), purpose=body.purpose, kind=body.kind, base_url=base, network=body.network,
                            use_egress=body.use_egress, priority=body.priority, enabled=body.enabled,
                            max_results=body.max_results, timeout_s=body.timeout_s, per_minute=body.per_minute,
                            per_day=body.per_day)
    db.add(row)
    await db.flush()
    if body.api_key:  # sealed for this row, so it needs the id
        row.api_key_enc = encrypt_instance(body.api_key.strip(), context=web_search_provider_context(row.id))
    await audit(db, p, "admin.web_search.add", str(row.id),
                {"purpose": row.purpose, "kind": row.kind, "base_url": row.base_url, "network": row.network,
                 "use_egress": row.use_egress, **_route_note(row)}, always=True)
    await db.commit()
    return provider_out(row)


@router.patch("/providers/{pid}")
async def update_provider(pid: int, body: ProviderIn, p: Principal = Depends(admin),
                          db: AsyncSession = Depends(get_db)):
    row = await db.get(WebSearchProvider, pid)
    if row is None:
        raise ApiError(404, "websearch.provider_not_found")
    purpose = row.purpose or "search"
    if row.from_env:
        # The server's reader: its name, URL, kind and token come from the environment; the admin
        # sets its route, order, limits and whether it is on.
        body = body.model_copy(update={"name": row.name, "kind": row.kind, "base_url": row.base_url,
                                       "network": row.network, "api_key": None})
    if body.kind not in _kinds(purpose):
        raise HTTPException(400, f"unknown kind {body.kind!r}")  # i18n: internal
    base = row.base_url if row.from_env else await _checked(body, body.kind, body.network, purpose)
    other = (await db.execute(select(WebSearchProvider.id).where(
        WebSearchProvider.name == body.name.strip(), WebSearchProvider.id != pid))).first()
    if other:
        raise ApiError(409, "websearch.provider_name_taken")
    before = {"base_url": row.base_url, "network": row.network, "kind": row.kind, "use_egress": row.use_egress,
              **_route_note(row)}
    if (base != row.base_url.rstrip("/") or body.kind != row.kind) and body.api_key is None:
        row.api_key_enc = None  # a stored key only ever goes to the URL it was saved for
    row.name, row.kind, row.base_url, row.network = body.name.strip(), body.kind, base, body.network
    row.use_egress, row.priority, row.enabled = body.use_egress, body.priority, body.enabled
    row.max_results, row.timeout_s, row.per_minute, row.per_day = (body.max_results, body.timeout_s,
                                                                   body.per_minute, body.per_day)
    if body.api_key is not None:
        row.api_key_enc = (encrypt_instance(body.api_key.strip(), context=web_search_provider_context(row.id))
                           if body.api_key.strip() else None)
    await audit(db, p, "admin.web_search.update", str(row.id), {"before": before, "after": {
        "base_url": row.base_url, "network": row.network, "kind": row.kind, "use_egress": row.use_egress,
        **_route_note(row), "key_changed": body.api_key is not None}}, always=True)
    await db.commit()
    return provider_out(row)


@router.delete("/providers/{pid}")
async def delete_provider(pid: int, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(WebSearchProvider, pid)
    if row is None:
        raise ApiError(404, "websearch.provider_not_found")
    if row.from_env:
        raise ApiError(409, "websearch.reader_from_env")
    await db.delete(row)
    await audit(db, p, "admin.web_search.delete", str(pid), {"name": row.name}, always=True)
    await db.commit()
    return {"ok": True}


def _route_note(row: WebSearchProvider) -> dict[str, Any]:
    """For the audit log: a browserless reader's route, so the choice "Directly from this network"
    (allowed under MAILR_REQUIRE_EGRESS) is on record."""
    if row.purpose == "reader" and row.kind == "browserless" and row.network == "local":
        return {"route": "egress" if row.use_egress else "direct"}
    return {}


async def _run_test(db: AsyncSession, prov: WebSearchProvider, query: str, key: str | None,
                    url: str = TEST_PAGE) -> dict[str, Any]:
    t0 = time.monotonic()
    if prov.purpose == "reader":
        try:
            page = await readers.read_with(db, prov, url, key=key, count_usage=False, max_chars=400)
        except service.ProviderError as e:
            return {"ok": False, "error": str(e), "ms": int((time.monotonic() - t0) * 1000)}
        return {"ok": True, "via": page["via"], "route": page.get("route"), "status": page.get("status"),
                "ms": int((time.monotonic() - t0) * 1000),
                "page": {"title": page["title"], "site": page["site"], "chars": page["chars"],
                         "text": page["text"]}}
    try:
        results, route = await service.ask(db, prov, query, 3, None, key=key, count_usage=False)
    except service.ProviderError as e:
        return {"ok": False, "error": str(e), "ms": int((time.monotonic() - t0) * 1000)}
    return {"ok": True, "via": route.via, "ms": int((time.monotonic() - t0) * 1000),
            "results": [r.as_dict() for r in results]}


@router.post("/providers/{pid}/test")
async def test_provider(pid: int, body: TestIn | None = None, p: Principal = Depends(admin),
                        db: AsyncSession = Depends(get_db)):
    """One search with a saved provider (not counted against its limits)."""
    row = await db.get(WebSearchProvider, pid)
    if row is None:
        raise ApiError(404, "websearch.provider_not_found")
    out = await _run_test(db, row, (body.query if body else TestIn().query), None, (body.url if body else TEST_PAGE))
    if out["ok"]:
        row.last_ok_at, row.last_error = datetime.now(UTC), ""
    else:
        row.last_error = out["error"][:500]
    await db.commit()
    return out


@router.post("/test")
async def test_draft(body: TestIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """One search with the settings in the dialog, before or after saving them."""
    stored = await db.get(WebSearchProvider, body.id) if body.id else None
    if body.id and stored is None:
        raise ApiError(404, "websearch.provider_not_found")
    purpose = (stored.purpose if stored else None) or body.purpose or "search"
    kinds = _kinds(purpose)
    kind = body.kind or (stored.kind if stored else "")
    if kind not in kinds:
        raise HTTPException(400, f"unknown kind {kind!r}")  # i18n: internal
    network = body.network or (stored.network if stored else kinds[kind].default_network)
    if stored is not None and stored.from_env:
        # The server's reader: only its route can differ from what is stored.
        kind, network, base = stored.kind, stored.network, stored.base_url
        draft = WebSearchProvider(id=stored.id, name=stored.name, purpose=purpose, kind=kind, base_url=base,
                                  network=network, from_env=True, max_results=3,
                                  use_egress=body.use_egress if body.use_egress is not None else stored.use_egress,
                                  timeout_s=body.timeout_s or stored.timeout_s)
        return await _run_test(db, draft, body.query, None, body.url)
    base = await _checked(TestIn(base_url=body.base_url or (stored.base_url if stored else "")), kind, network,
                          purpose)
    key = body.api_key.strip() if body.api_key else None
    if key is None and stored is not None and stored.api_key_enc:
        if base != stored.base_url.rstrip("/") or kind != stored.kind:
            raise ApiError(400, "websearch.api_key_again")
        key = service.key_of(stored)
    draft = WebSearchProvider(id=stored.id if stored else 0, name=(stored.name if stored else kinds[kind].label),
                              purpose=purpose, kind=kind, base_url=base, network=network,
                              use_egress=body.use_egress if body.use_egress is not None
                              else (stored.use_egress if stored else True),
                              max_results=3, timeout_s=body.timeout_s or (stored.timeout_s if stored else 10))
    return await _run_test(db, draft, body.query, key, body.url)
