"""May an image reach the built-in assistant's model?

Three conditions, all of them:

1. the model the next call goes to can view images (intel/modelcaps.py: detected or set by an
   admin);
2. the person's setting "Let the assistant see images" (`assistant_images`, on by default) is on;
3. the outbound policy allows the content to go to that model (core/outbound.allowed): the call is
   resolved with the content's class, so local-only content is only ever checked against a local
   model, and the policy is restated here.

The model depends on the chat's sensitivity: a chat that has read local-only content uses the
local model, which may differ from the hosted one. `can_view` therefore takes the class of the
call that will carry the image (the session's, raised by the image's own class).

The REST tool API and the MCP server return images to their caller as image blocks; none of this
applies there (ToolCtx.images is set for them).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import outbound
from mailr.intel import llm, modelcaps

# At most this many images reach the model in one question (tool results and the user's own
# attachments together), each with its long side at most DEFAULT_PX (MAX_PX when the model asks).
MAX_PER_TURN = 4
DEFAULT_PX = 1024
MAX_PX = 1600
# About how many prompt tokens one image takes at DEFAULT_PX (agent/loop.prompt_tokens).
IMAGE_TOKENS = 1200


def setting_on(user: Any) -> bool:
    return (getattr(user, "settings", None) or {}).get("assistant_images") is not False


async def model_can_view(db: AsyncSession, user: Any, *, sensitive: bool) -> bool:
    """Can the chat model that a call of this class resolves to view images? Only the model and
    the policy; not the person's setting."""
    try:
        r = await llm.resolve(db, user, "chat", sensitive=sensitive, probe=True)
    except (llm.NoModel, llm.LocalOnlyViolation):
        return False
    if not outbound.allowed(sensitive or outbound.tainted(), r.endpoint):
        return False  # the guard, restated: never reached, resolve already refused
    return modelcaps.can_view_images(r.endpoint, r.model)


async def can_view(db: AsyncSession, user: Any, *, sensitive: bool) -> bool:
    """All three conditions for content of this class."""
    if user is None or not setting_on(user):
        return False
    return await model_can_view(db, user, sensitive=sensitive)


def image_part(mime: str, b64: str) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
