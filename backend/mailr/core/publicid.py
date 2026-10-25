"""Public identifiers for conversations, messages and approvals (pending actions).

Inside mailr, conversations, messages and approvals have sequential integer ids. Outside (the web app,
Android, REST, MCP, links) they appear as opaque numbers: a keyed permutation of the id, its
kind and a check value, so ids cannot be guessed, counted or swapped between kinds without the
server key. Ownership is still checked on every request; this is a second layer.

Shape: a 52-bit block encrypted with an 8-round Feistel network (HMAC-SHA256 round function,
key derived from MAILR_SECRET_KEY), plus 2**52, so every public id lies in [2**52, 2**53):
16 digits, exact in JavaScript and JSON doubles, and never equal to an internal id (< 2**40).

    plaintext = kind (2 bits) | check (10 bits) | id (40 bits)

Enabled with MAILR_PUBLIC_IDS=true. While MAILR_PUBLIC_IDS_LEGACY=true, plain internal ids are
still accepted on input (old links, not yet updated apps); turn it off once clients are updated.
Approvals have one more allowance (api/permission_routes.py): a numeric id is still accepted for
an approval that is pending and unexpired, so notifications sent before the switch can be decided.
The translation happens at the edge only (core/publicid_http.py); the rest of the code keeps
integer ids.
"""

from __future__ import annotations

import hashlib
import hmac
from functools import lru_cache
from typing import Any

from mailr.config import get_settings

KINDS = {"conversation": 1, "message": 2, "approval": 3}
_BASE = 1 << 52
_HALF = 26
_MASK = (1 << _HALF) - 1
_ID_BITS = 40
_ROUNDS = 8


class InvalidId(ValueError):
    """Not a valid public id for this kind (answered as 404 by the HTTP layer)."""


def enabled() -> bool:
    return bool(get_settings().public_ids)


def legacy_allowed() -> bool:
    return bool(get_settings().public_ids_legacy)


@lru_cache(maxsize=4)
def _key(secret: str) -> bytes:
    return hmac.new(secret.encode(), b"mailr-public-id-v1", hashlib.sha256).digest()


def _k() -> bytes:
    secret = get_settings().secret_key
    if not secret:
        raise RuntimeError("MAILR_PUBLIC_IDS needs MAILR_SECRET_KEY")
    return _key(secret)


def _f(key: bytes, rnd: int, half: int) -> int:
    d = hmac.new(key, bytes([rnd]) + half.to_bytes(4, "big"), hashlib.sha256).digest()
    return int.from_bytes(d[:4], "big") & _MASK


def _check(key: bytes, kind: int, n: int) -> int:
    d = hmac.new(key, b"c" + bytes([kind]) + n.to_bytes(5, "big"), hashlib.sha256).digest()
    return int.from_bytes(d[:2], "big") & 0x3FF


def _encrypt(key: bytes, x: int) -> int:
    left, right = x >> _HALF, x & _MASK
    for r in range(_ROUNDS):
        left, right = right, left ^ _f(key, r, right)
    return (left << _HALF) | right


def _decrypt(key: bytes, y: int) -> int:
    left, right = y >> _HALF, y & _MASK
    for r in reversed(range(_ROUNDS)):
        left, right = right ^ _f(key, r, left), left
    return (left << _HALF) | right


def encode(kind: str, n: int | None) -> int | None:
    """Internal id → public id (unchanged when public ids are off, or already public)."""
    if n is None or isinstance(n, bool) or not enabled():
        return n
    n = int(n)
    if n >= _BASE or n < 0:  # already public (encoding is idempotent), or not an id
        return n
    if n >= 1 << _ID_BITS:
        raise ValueError("id out of range")
    key = _k()
    k = KINDS[kind]
    plain = (k << 50) | (_check(key, k, n) << _ID_BITS) | n
    return _BASE + _encrypt(key, plain)


def decode(kind: str, v: Any) -> int:
    """Public id (or, while legacy is allowed, an internal id) → internal id."""
    if isinstance(v, bool):
        raise InvalidId(kind)
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise InvalidId(kind) from None
    if not enabled():
        return n
    if n < _BASE:
        if legacy_allowed() and n >= 0:
            return n
        raise InvalidId(kind)
    if n >= _BASE * 2:
        raise InvalidId(kind)
    key = _k()
    plain = _decrypt(key, n - _BASE)
    k, chk, ident = plain >> 50, (plain >> _ID_BITS) & 0x3FF, plain & ((1 << _ID_BITS) - 1)
    if k != KINDS[kind] or chk != _check(key, k, ident):
        raise InvalidId(kind)
    return ident


# ---------- JSON walking (by key name) ----------

CONV_KEYS = frozenset({"conversation_id", "conversation_ids", "source_conversation_id",
                       # the assistant's view of the screen (agent/screen.py)
                       "open_conversation_ids", "selected_conversation_ids", "list_conversation_ids",
                       "brief_conversation_id",
                       # conversations a bulk action or review leaves alone (agent/view_tools.py, review.py)
                       "except_ids"})
MSG_KEYS = frozenset({"message_id", "message_ids", "draft_id", "in_reply_to_message_id",
                      "forward_of_message_id", "reply_to_message_id", "source_message_id", "match_message_id"})
# Approvals (pending actions): agent and MCP results, chat steps, push payloads, chat resumes.
APPROVAL_KEYS = frozenset({"approval_id", "approval_ids", "pending_id", "resume_approval_id"})
# Objects that name an approval by their "id" (the assistant's screen context).
APPROVAL_OBJ_KEYS = frozenset({"focused_approval"})


class ConvId(int):
    """An int that is a conversation id wherever it sits (e.g. under "id")."""


class MsgId(int):
    """An int that is a message id wherever it sits."""


class ApprovalId(int):
    """An int that is an approval (pending action) id wherever it sits."""


def _is_id(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _map(kind: str, v: Any, fn) -> Any:
    if _is_id(v):
        return fn(kind, v)
    if isinstance(v, list) and all(_is_id(x) for x in v):
        return [fn(kind, x) for x in v]
    return v


def out(obj: Any) -> Any:
    """Encode ids in a response object: ConvId/MsgId anywhere, and ints under the id keys.
    (message_out's "message_id" is the Message-ID header, a string, and is left alone.)"""
    if not enabled():
        return obj
    if isinstance(obj, ConvId):
        return encode("conversation", int(obj))
    if isinstance(obj, MsgId):
        return encode("message", int(obj))
    if isinstance(obj, ApprovalId):
        return encode("approval", int(obj))
    if isinstance(obj, dict):
        res = {}
        for k, v in obj.items():
            if k in CONV_KEYS:
                res[k] = _map("conversation", v, encode)
            elif k in MSG_KEYS:
                res[k] = _map("message", v, encode)
            elif k in APPROVAL_KEYS:
                res[k] = _map("approval", v, encode)
            elif k in APPROVAL_OBJ_KEYS:
                res[k] = ({**v, "id": _map("approval", v.get("id"), encode)} if isinstance(v, dict)
                          else _map("approval", v, encode))
            else:
                res[k] = out(v)
        return res
    if isinstance(obj, (list, tuple)):
        return [out(x) for x in obj]
    return obj


# Context lists (what the screen shows): a stale id there is dropped, not a 404 for the request.
LENIENT_KEYS = frozenset({"open_conversation_ids", "selected_conversation_ids", "list_conversation_ids"})


def _try_decode(kind: str, v: Any) -> int | None:
    try:
        return decode(kind, v) if (_is_id(v) or (isinstance(v, str) and v.isdigit())) else None
    except InvalidId:
        return None


def into(obj: Any, extra: dict[str, str] | None = None) -> Any:
    """Decode ids in a request object by key; `extra` maps more keys (e.g. "ids") to a kind."""
    if not enabled():
        return obj
    if isinstance(obj, dict):
        res = {}
        for k, v in obj.items():
            kind = ("conversation" if k in CONV_KEYS else "message" if k in MSG_KEYS
                    else "approval" if k in APPROVAL_KEYS else (extra or {}).get(k))
            if k in APPROVAL_OBJ_KEYS:
                # A stale or foreign approval on the screen is dropped, not a 404 for the turn.
                raw = v.get("id") if isinstance(v, dict) else v
                aid = _try_decode("approval", raw)
                if aid is None:
                    continue
                res[k] = {**v, "id": aid} if isinstance(v, dict) else aid
            elif k in LENIENT_KEYS and isinstance(v, list):
                res[k] = [x for x in (_try_decode(kind, i) for i in v) if x is not None]
            elif kind and (_is_id(v) or (isinstance(v, str) and v.isdigit())):
                res[k] = decode(kind, v)
            elif kind and isinstance(v, list) and all(_is_id(x) or (isinstance(x, str) and x.isdigit()) for x in v):
                res[k] = [decode(kind, x) for x in v]
            else:
                res[k] = into(v, extra)
        return res
    if isinstance(obj, list):
        return [into(x, extra) for x in obj]
    return obj


_B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_PREFIX = {"conversation": "c", "message": "m", "approval": "a"}


def token(kind: str, n: int) -> str:
    """The short form used in URLs: a kind letter plus base62 of the public id (c3kFq…).
    With public ids off it is the plain number. The web app reads both (lib/pubid.ts)."""
    v = encode(kind, n)
    if not enabled():
        return str(v)
    s = ""
    while v:
        v, r = divmod(v, 62)
        s = _B62[r] + s
    return _PREFIX[kind] + s


def from_token(kind: str, t: str) -> int:
    """A URL short form (or a plain number) → internal id."""
    t = (t or "").strip()
    if t.isascii() and t.isdigit():
        if len(t) > 18:  # nothing longer is an id (public ids have 16 digits)
            raise InvalidId(kind)
        return decode(kind, int(t))
    # The short form exists only with public ids on, and is never longer than 10 characters.
    if (not enabled() or len(t) < 2 or len(t) > 12 or t[0] != _PREFIX[kind]
            or any(ch not in _B62 for ch in t[1:])):
        raise InvalidId(kind)
    v = 0
    for ch in t[1:]:
        v = v * 62 + _B62.index(ch)
    return decode(kind, v)
