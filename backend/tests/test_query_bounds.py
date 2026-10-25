"""A negative or zero `limit` (and a negative offset or cursor) is a 422, never a crash or a
silent LIMIT 0. On /changes a LIMIT 0 answered the current cursor with no changes, so a client
skipped every pending change."""

import pytest

BAD = [
    "/api/v1/changes?since=0&limit=-1",
    "/api/v1/changes?since=0&limit=0",
    "/api/v1/changes?since=-5",
    "/api/v1/people?limit=-1",
    "/api/v1/people?offset=-1",
    "/api/v1/smart/needs_reply?limit=-1",
    "/api/v1/suggestions?limit=-1",
    "/api/v1/search/semantic?q=x&limit=-1",
    "/api/v1/groups?limit=-1",
    "/api/v1/contacts/places?limit=-1",
    "/api/v1/approvals?limit=-1",
    "/api/v1/assistant/runs?limit=-1",
    "/api/v1/assistant/memories?limit=-1",
    "/api/v1/agent/sessions?limit=-1",
    "/api/v1/audit?limit=-1",
]


@pytest.mark.parametrize("url", BAD)
async def test_negative_limit_is_rejected(client, url):
    r = await client.get(url)
    assert r.status_code == 422, (url, r.status_code, r.text[:200])


async def test_changes_with_a_valid_limit_still_answers(client):
    r = await client.get("/api/v1/changes", params={"since": 0, "limit": 1})
    assert r.status_code == 200


async def test_routes_called_directly_keep_int_defaults(client, user):
    """The agent's find_people calls the /people route function directly: the bounded parameters
    keep plain int defaults (Annotated[int, Query(...)]), not Query objects."""
    from mailr.api.org_routes import people

    assert people.__defaults__ is None or all(not type(d).__name__ == "Query" for d in people.__defaults__)
