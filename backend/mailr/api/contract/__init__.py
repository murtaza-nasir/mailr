"""The typed API contract: Pydantic models of what the routes answer (backend/README.md, "Typed API contract").

A route takes part by naming its model, `response_model=X` with `**TYPED` (below). Its OpenAPI
document then carries the response schema, frontend/src/api/schema.gen.ts gets the TypeScript
type (tools/api-types), and tests/test_api_contract.py checks a real response against it.
"""

from __future__ import annotations

from typing import Any

from mailr.api.contract import account, calendar, mail, notes

# What every typed route passes with its response_model: a field the route leaves out stays out
# (instead of going out as null), so typing a route never changes what it sends.
TYPED: dict[str, Any] = {"response_model_exclude_unset": True}

__all__ = ["TYPED", "account", "calendar", "mail", "notes"]
