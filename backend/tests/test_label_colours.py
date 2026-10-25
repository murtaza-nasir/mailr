"""Label colour schemes: scheme tokens next to the legacy hex forms,
the scheme switch that moves token colours, and the user-confirmed conversion of old colours."""

import json
from pathlib import Path

import pytest
from helpers.audit_fixes import _token

from mailr.core import label_colours

DATA = Path(__file__).resolve().parents[2] / "spec/fixtures/label-colours.json"


def test_ids_match_the_shared_data_file():
    data = json.loads(DATA.read_text())
    assert tuple(s["id"] for s in data["schemes"]) == label_colours.SCHEMES
    assert tuple(data["hues"]) == label_colours.HUES
    assert data["shades"] == label_colours.SHADES
    for s in data["schemes"]:
        assert [h["id"] for h in s["hues"]] == list(label_colours.HUES)
        assert all(len(h["light"]) == len(h["dark"]) == label_colours.SHADES for h in s["hues"])


@pytest.mark.parametrize("value,stored", [
    ("@default/teal/3", "@default/teal/3"),
    (" @Earth/Red/1 ", "@earth/red/1"),
    ("#FB4C2F", "#fb4c2f"),
    ("#fb4c2f,#FFFFFF", "#fb4c2f,#ffffff"),
    ("#fb4c2f|#ffffff", "#fb4c2f,#ffffff"),
    ("", None),
    (None, None),
])
def test_normalize_accepts_tokens_and_legacy(value, stored):
    assert label_colours.normalize(value) == stored


@pytest.mark.parametrize("value", ["red", "@default/teal/5", "@default/teal/0", "@neon/teal/2", "@default/cyan/2",
                                   "@default/teal", "#fff", "#12345g", "@default/teal/3,#ffffff"])
def test_normalize_rejects(value):
    with pytest.raises(ValueError):
        label_colours.normalize(value)


def test_rescheme_keeps_hue_and_shade():
    assert label_colours.rescheme("@default/teal/3", "earth") == "@earth/teal/3"
    assert label_colours.rescheme("#16a766,#ffffff", "earth") is None
    assert label_colours.rescheme(None, "earth") is None


async def test_label_routes_store_tokens_and_legacy(client, user):
    lb = (await client.post("/api/v1/labels", json={"name": "Tokened", "color": "@ocean/blue/2"})).json()
    assert lb["color"] == "@ocean/blue/2"
    legacy = (await client.post("/api/v1/labels", json={"name": "Coloured", "color": "#FB4C2F|#FFFFFF"})).json()
    assert legacy["color"] == "#fb4c2f,#ffffff"  # Gmail's form, stored lower-case with a comma
    r = await client.patch(f"/api/v1/labels/{lb['id']}", json={"color": "#16a766,#ffffff"})
    assert r.json()["color"] == "#16a766,#ffffff"
    r = await client.patch(f"/api/v1/labels/{lb['id']}", json={"color": "@vivid/pink/4"})
    assert r.json()["color"] == "@vivid/pink/4"
    r = await client.patch(f"/api/v1/labels/{lb['id']}", json={"color": "@vivid/pink/9"})
    assert r.status_code == 400 and "@default/teal/3" in r.json()["detail"]
    assert (await client.patch(f"/api/v1/labels/{lb['id']}", json={"color": "red"})).status_code == 400
    r = await client.patch(f"/api/v1/labels/{lb['id']}", json={"color": ""})
    assert r.status_code == 200 and r.json()["color"] is None


async def test_scheme_switch_moves_token_labels_only(client, user):
    a = (await client.post("/api/v1/labels", json={"name": "A", "color": "@default/teal/3"})).json()
    b = (await client.post("/api/v1/labels", json={"name": "B", "color": "#fb4c2f,#ffffff"})).json()
    c = (await client.post("/api/v1/labels", json={"name": "C"})).json()
    me = (await client.get("/api/v1/me")).json()
    assert me["settings"]["label_scheme"] == "default"
    r = await client.post("/api/v1/labels/colours", json={"scheme": "earth"})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == [a["id"]]
    labels = {x["id"]: x["color"] for x in (await client.get("/api/v1/labels")).json()}
    assert labels[a["id"]] == "@earth/teal/3"
    assert labels[b["id"]] == "#fb4c2f,#ffffff"  # an old colour changes only through the conversion
    assert labels[c["id"]] is None
    assert (await client.get("/api/v1/me")).json()["settings"]["label_scheme"] == "earth"


async def test_conversion_applies_the_confirmed_colours(client, user):
    a = (await client.post("/api/v1/labels", json={"name": "Old", "color": "#fb4c2f,#ffffff"})).json()
    b = (await client.post("/api/v1/labels", json={"name": "Older", "color": "#c9daf8,#1c4587"})).json()
    r = await client.post("/api/v1/labels/colours", json={"colors": {str(a["id"]): "@default/red/4",
                                                                     str(b["id"]): "@default/blue/2"}})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["changed"]) == sorted([a["id"], b["id"]])
    labels = {x["id"]: x["color"] for x in (await client.get("/api/v1/labels")).json()}
    assert labels[a["id"]] == "@default/red/4" and labels[b["id"]] == "@default/blue/2"
    # One bad colour or unknown label and nothing changes.
    r = await client.post("/api/v1/labels/colours", json={"colors": {str(a["id"]): "@default/red/7"}})
    assert r.status_code == 400
    r = await client.post("/api/v1/labels/colours", json={"colors": {"999999": "@default/red/1"}})
    assert r.status_code == 404
    assert (await client.post("/api/v1/labels/colours", json={"scheme": "neon"})).status_code == 400


async def test_scheme_is_not_set_through_settings_or_tokens(client, user):
    r = await client.patch("/api/v1/me/settings", json={"label_scheme": "earth"})
    assert r.status_code == 400
    t = await _token(client, ["label"])
    assert (await t.post("/api/v1/labels/colours", json={"scheme": "earth"})).status_code == 403
    # A token still sets one label's colour (a scheme colour included) through PATCH.
    lb = (await client.post("/api/v1/labels", json={"name": "Agent"})).json()
    r = await t.patch(f"/api/v1/labels/{lb['id']}", json={"color": "@muted/green/2"})
    assert r.status_code in (200, 202), r.text


async def test_null_clears_and_absent_keeps_the_colour(client, user):
    lb = (await client.post("/api/v1/labels", json={"name": "Keep", "color": "@default/green/2"})).json()
    r = await client.patch(f"/api/v1/labels/{lb['id']}", json={"show_in_list": "unread"})
    assert r.json()["color"] == "@default/green/2"
    r = await client.patch(f"/api/v1/labels/{lb['id']}", json={"color": None})
    assert r.json()["color"] is None
