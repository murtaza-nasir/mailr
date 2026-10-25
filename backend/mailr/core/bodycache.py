"""A per-process cache of rendered message bodies (core/render.render_body).

Rendering is the expensive part of GET /messages/{id}/body: parsing and sanitising a 30 KB
newsletter takes 30 ms on the workstation and 100-200 ms in production, and the reader asks for
the same body again on every open. The output depends only on:

- the message's raw bytes (fixed for a message id and its blob id: a new blob is a new key);
- whether remote images are allowed (after the egress check) and the proxy path, which holds
  the message's public id (both in the key);
- the image proxy's cache entries for the message's images (failed images and 1x1 pixels are
  dropped): their states are kept with the entry and checked again on every hit;
- RENDER_VERSION, bumped with any change to the sanitiser.

Nothing per user or per request goes in: no CSP nonce, no token, no highlight. The `junk`,
`images_paused` and `untrusted` fields are added by the route after the cache, on every request.
A message id belongs to one user, and the routes authorise the request (load_message) before
they ask the cache, so a hit never reaches anyone who could not have rendered the body.

Bounded by bytes and by age; entries live in the worker's memory only, beside the raw-message
cache in core/store.py, which already holds the same mail.
"""

from __future__ import annotations

import copy
import time
from collections import OrderedDict
from typing import Any

from mailr.core.render import RENDER_VERSION, image_state

MAX_BYTES = 32 * 1024 * 1024
MAX_AGE = 3600.0  # seconds


class _Entry:
    __slots__ = ("at", "deps", "out", "size")

    def __init__(self, out: dict[str, Any], deps: dict[str, tuple | None], size: int):
        self.out, self.deps, self.size, self.at = out, deps, size, time.monotonic()


class BodyCache:
    def __init__(self, max_bytes: int = MAX_BYTES, max_age: float = MAX_AGE):
        self.max = max_bytes
        self.max_age = max_age
        self.size = 0
        self.items: OrderedDict[tuple, _Entry] = OrderedDict()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(message_id: int, blob_id: str | None, allow: bool, proxy_base: str | None,
            part: int | None = None) -> tuple:
        return (RENDER_VERSION, message_id, blob_id or "", part, bool(allow), proxy_base or "")

    def get(self, k: tuple) -> dict[str, Any] | None:
        """A copy of the cached output, or None when there is none, it is too old, or an image it
        depends on changed state in the image proxy's cache."""
        e = self.items.get(k)
        if e is not None and (time.monotonic() - e.at > self.max_age
                              or any(image_state(h) != s for h, s in e.deps.items())):
            self._drop(k)
            e = None
        if e is None:
            self.misses += 1
            return None
        self.items.move_to_end(k)
        self.hits += 1
        return copy.deepcopy(e.out)

    def put(self, k: tuple, out: dict[str, Any], deps: dict[str, tuple | None]) -> None:
        size = sum(len(v) for v in out.values() if isinstance(v, str)) + 64 * len(deps) + 256
        if size > self.max // 8:
            return
        self._drop(k)
        self.items[k] = _Entry(copy.deepcopy(out), dict(deps), size)
        self.size += size
        while self.size > self.max and self.items:
            self._drop(next(iter(self.items)))

    def _drop(self, k: tuple) -> None:
        e = self.items.pop(k, None)
        if e is not None:
            self.size -= e.size

    def clear(self) -> None:
        self.items.clear()
        self.size = 0


bodies = BodyCache()
