"""The building blocks of the response models (backend/README.md, "Typed API contract").

- `Out`: the base class. A field the model does not list still goes out unchanged (a route may
  add one before its model does), but the schema leaves it out, and the contract test
  (tests/test_api_contract.py) fails until the model lists it.
- `PublicId`: an id that may be a conversation, message or approval id (core/publicid.py). The
  serialisers return ConvId/MsgId objects, and a plain `int` field would turn them into plain
  ints before the public-id encoding sees their kind; this type keeps the kind and encodes it.
  Ids under the id keys (`conversation_id`, `message_ids`, ...) stay plain `int`: the HTTP layer
  encodes those by key name.
- Times are ISO 8601 strings (`IsoTime`), as the serialisers write them; the models never parse
  and re-format them (a datetime object goes out as isoformat(), as FastAPI wrote it before).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer, WrapValidator


def _schema_without_extra(schema: dict[str, Any]) -> None:
    schema.pop("additionalProperties", None)


class Out(BaseModel):
    """A response object. Unknown fields pass through; the schema lists only the declared ones."""

    model_config = ConfigDict(extra="allow", json_schema_extra=_schema_without_extra, populate_by_name=True)


def _keep_id_kind(v: Any, handler: Any) -> Any:
    out = handler(v)
    # ConvId, MsgId and ApprovalId are int subclasses: keep the object, so the serialiser below
    # knows which kind of public id it is.
    return v if isinstance(v, int) and not isinstance(v, bool) and type(v) is not int else out


def _encode_id(v: int) -> int:
    from mailr.core import publicid

    return publicid.out(v)


PublicId = Annotated[int, WrapValidator(_keep_id_kind), PlainSerializer(_encode_id, return_type=int),
                     Field(description="An id; conversations, messages and approvals carry their public id.")]

def _iso(v: Any) -> Any:
    # A datetime or date the serialiser left as it is goes out as FastAPI's encoder wrote it before
    # the route had a model: isoformat().
    return v.isoformat() if isinstance(v, date) else v


IsoTime = Annotated[str, BeforeValidator(_iso), Field(description="An ISO 8601 time, as the server wrote it.")]
IsoDate = Annotated[str, BeforeValidator(_iso), Field(description="An ISO 8601 date (YYYY-MM-DD).")]

# A part of a response that has no model yet: it goes out as the route built it.
Untyped = dict[str, Any]


def absent(description: str | None = None, **kw: Any) -> Any:
    """The default of a field the route leaves out when it has nothing to say (the key is absent, not
    null). With `exclude_unset` (contract.TYPED) it stays absent, and TypeScript gets `field?: T`.
    Declare the type without `| None` unless the route also sends null."""
    return Field(None, description=description, **kw)
