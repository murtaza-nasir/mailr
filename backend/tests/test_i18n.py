"""The server's own texts in the reader's language (mailr/i18n).

Like Speakr's tests/test_localization.py, the catalogue checks run without a browser and do not
depend on any particular key:

1. every key the code asks for (`i18n.t("…")`, `ApiError(…, "code")`) exists in English;
2. every other language has exactly the keys English has, no empty text, and only `{{name}}`
   placeholders (a single-brace `{name}` is shown to the reader as written);
3. a key with plural forms has its `_other` form.

Then the behaviour: coded API errors, GET /i18n/messages, the `language` setting, push text in the
recipient's language, and Ambrose's reply language.
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from mailr import i18n

CODE = Path(i18n.__file__).parent.parent
T_CALL = re.compile(r"""\bi18n\.(?:t|msg|text)\(\s*(f?)["']([a-z_][\w.{}]*)["']""")
API_ERROR = re.compile(r"""\bApiError\(\s*\d{3}\s*,\s*(f?)["']([a-z_][\w.{}]*)["']""")
SINGLE_BRACE = re.compile(r"(?<!\{)\{[A-Za-z_]\w*\}(?!\})")
PLURAL = re.compile(r"_(zero|one|two|few|many|other)$")


def _referenced() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for p in CODE.rglob("*.py"):
        if "i18n" in p.parts or "dev" in p.parts:
            continue
        src = p.read_text()
        for m in T_CALL.finditer(src):
            if not m[1]:
                found.setdefault(m[2], set()).add(str(p.relative_to(CODE)))
        for m in API_ERROR.finditer(src):
            if not m[1]:
                found.setdefault(f"errors.{m[2]}", set()).add(str(p.relative_to(CODE)))
    return found


def _languages() -> list[str]:
    return i18n.available()


def test_there_is_an_english_catalogue():
    assert _languages()[0] == "en"
    assert len(_languages()) > 1  # the per-language checks below have something to check
    assert len(i18n.catalogue("en")) > 100


def test_the_scanner_finds_keys():
    """Guards the next test from passing while checking nothing."""
    refs = _referenced()
    assert len(refs) > 60, f"only {len(refs)} keys found; the scanner is broken"
    assert "errors.mail.conversation_not_found" in refs and "push.mail.title" in refs


def test_every_key_the_code_asks_for_is_in_english():
    missing = {k: v for k, v in _referenced().items() if not i18n.has(k)}
    assert not missing, "asked for but absent from mailr/i18n/en.json:\n" + "\n".join(
        f"  {k}  <- {', '.join(sorted(v))}" for k, v in sorted(missing.items()))


@pytest.mark.parametrize("lang", [x for x in _languages() if x != "en"])
def test_each_language_has_exactly_the_english_keys(lang):
    en, other = set(i18n.catalogue("en")), set(i18n.catalogue(lang))
    # A language may have plural forms English lacks (few, many) and lack ones English has (one).
    base = lambda keys: {PLURAL.sub("", k) for k in keys}
    assert not sorted(base(en) - base(other)), f"{lang}.json lacks {sorted(base(en) - base(other))[:15]}"
    assert not sorted(base(other) - base(en)), f"{lang}.json has extra {sorted(base(other) - base(en))[:15]}"


@pytest.mark.parametrize("lang", _languages())
def test_no_text_is_empty_and_placeholders_use_double_braces(lang):
    table = i18n.catalogue(lang)
    assert not [k for k, v in table.items() if not v.strip()], "empty texts"
    bad = [f"{k}: {v}" for k, v in table.items() if SINGLE_BRACE.search(v)]
    assert not bad, f"single-brace placeholders are never filled: {bad[:10]}"


@pytest.mark.parametrize("lang", _languages())
def test_plural_keys_have_their_other_form(lang):
    table = i18n.catalogue(lang)
    stems = {PLURAL.sub("", k) for k in table if PLURAL.search(k)}
    assert not [s for s in stems if f"{s}_other" not in table]


def test_the_catalogue_is_valid_json_with_no_duplicate_keys():
    def no_dupes(pairs):
        keys = [k for k, _ in pairs]
        assert len(keys) == len(set(keys)), f"duplicate keys: {keys}"
        return dict(pairs)

    for p in Path(i18n.__file__).parent.glob("*.json"):
        json.loads(p.read_text(encoding="utf-8"), object_pairs_hook=no_dupes)


# ---------------------------------------------------------------- the lookup itself


@pytest.fixture
def two_languages(tmp_path, monkeypatch):
    """English and a made-up German catalogue, in a folder of their own."""
    en = {"push": {"x": "Hello {{name}}", "n_one": "{{count}} message", "n_other": "{{count}} messages"},
          "only_en": "English only"}
    de = {"push": {"x": "Hallo {{name}}", "n_one": "{{count}} Nachricht", "n_other": "{{count}} Nachrichten"}}
    (tmp_path / "en.json").write_text(json.dumps(en))
    (tmp_path / "de.json").write_text(json.dumps(de))
    monkeypatch.setattr(i18n, "HERE", tmp_path)
    i18n._cache.clear()
    yield
    i18n._cache.clear()


def test_lookup_fills_params_picks_plurals_and_falls_back_to_english(two_languages):
    assert i18n.t("push.x", "de", name="Ana") == "Hallo Ana"
    assert i18n.t("push.n", "de", count=1) == "1 Nachricht"
    assert i18n.t("push.n", "de", count=5) == "5 Nachrichten"
    assert i18n.t("only_en", "de") == "English only"
    assert i18n.t("no.such.key", "de") == "no.such.key"
    assert i18n.available() == ["en", "de"]
    assert i18n.section("push", "de")["x"] == "Hallo {{name}}"


def test_language_matching_and_accept_language(two_languages):
    assert i18n.match("de-AT") == "de"
    assert i18n.match("DE") == "de"
    assert i18n.match("fr") is None
    assert i18n.match("auto") is None
    assert i18n.accept_language("fr-FR,fr;q=0.9,de;q=0.8,en;q=0.5") == "de"
    assert i18n.accept_language("*") is None
    assert i18n.user_language(SimpleNamespace(settings={"language": "de"})) == "de"
    assert i18n.user_language(SimpleNamespace(settings={"language": "fr"})) == "en"
    assert i18n.user_language(SimpleNamespace(settings={})) == "en"


def test_plural_rules():
    assert [i18n.plural_category("en", n) for n in (0, 1, 2)] == ["other", "one", "other"]
    assert [i18n.plural_category("fr", n) for n in (0, 1, 2)] == ["one", "one", "other"]
    assert [i18n.plural_category("ru", n) for n in (1, 3, 5, 21)] == ["one", "few", "many", "one"]
    assert [i18n.plural_category("pl", n) for n in (1, 2, 5, 22)] == ["one", "few", "many", "few"]
    assert i18n.plural_category("ja", 1) == "other"


def test_the_language_setting_is_checked():
    assert i18n.clean_language("pt-BR") == "pt-BR"
    assert i18n.clean_language("") is None and i18n.clean_language("auto") is None
    with pytest.raises(ValueError):
        i18n.clean_language("not a language")


# ---------------------------------------------------------------- over the API


async def test_a_coded_error_keeps_its_english_detail_and_adds_the_code(client):
    r = await client.patch("/api/v1/labels/999999999", json={"name": "x"})
    assert r.status_code == 404
    assert r.json() == {"detail": "label not found", "code": "labels.label_not_found", "params": {}}


async def test_the_messages_endpoint_serves_the_error_texts(client):
    r = await client.get("/api/v1/i18n/messages", params={"lang": "xx"})
    assert r.status_code == 200
    body = r.json()
    assert body["lang"] == "en" and body["version"]
    assert body["messages"]["labels.label_not_found"] == "label not found"
    assert body["messages"]["auth.password_too_short"] == "password must be at least {{min}} characters"
    r = await client.get("/api/v1/i18n/languages")
    assert r.json()["languages"][0] == "en"


async def test_the_language_setting_saves_clears_and_refuses_junk(client):
    r = await client.patch("/api/v1/me/settings", json={"language": "de"})
    assert r.status_code == 200 and r.json()["language"] == "de"
    r = await client.patch("/api/v1/me/settings", json={"language": "not a tag"})
    assert r.status_code == 400
    r = await client.patch("/api/v1/me/settings", json={"language": None})
    assert r.status_code == 200 and "language" not in r.json()


# ---------------------------------------------------------------- texts the server renders


def test_push_texts_render_in_the_recipients_language(two_languages, tmp_path):
    from mailr.core import push

    de = json.loads((tmp_path / "de.json").read_text())
    de["push"]["mail"] = {"title": "Mail von {{name}}", "body": "Neue Nachricht"}
    (tmp_path / "de.json").write_text(json.dumps(de))
    en = json.loads((Path(i18n.__file__).parent.parent / "i18n" / "en.json").read_text())
    (tmp_path / "en.json").write_text(json.dumps(en))
    i18n._cache.clear()
    assert push.mail_title("Ana", "ana@example.org", "de") == "Mail von Ana"
    assert push.mail_title("Ana", "ana@example.org") == "Mail from Ana"
    # A key German lacks reads in English.
    assert push.private_text("approval", "de") == ("Approval needed", "Open Mailr to review it.")


def test_english_push_texts_are_unchanged():
    from mailr.core import push

    assert push.private_text("follow_up") == ("No reply yet", "Open Mailr to see the conversation.")
    assert push.private_text("something_new") == ("Mailr", "Open Mailr to read it.")
    assert i18n.t("push.approval.to_more", count=2, first="a@example.org") == "To a@example.org and 2 more."
    assert i18n.t("digest.screener", count=1, url="U") == "**Screener:** 1 new sender waiting ([review](U))"


def test_ambrose_answers_in_the_chosen_language():
    from mailr.agent.prompts import assistant_system, language_note

    de = SimpleNamespace(name="Ana", email="ana@example.org", settings={"language": "de"})
    assert "German (de)" in language_note(de)
    assert "German (de)" in assistant_system(de, {})
    for s in ({}, {"language": "en-GB"}, {"language": None}, {"language": "bad tag!"}, {"language": "ar-XB"}):
        assert language_note(SimpleNamespace(name="Ana", email="ana@example.org", settings=s)) == ""


# ---------------------------------------------------------------- the shipped languages

SHIPPED = ["de", "es", "fr", "pt-BR", "ru", "zh-Hans"]
FORMS = {"ru": {"one", "few", "many", "other"}, "zh-Hans": {"other"}}


def test_the_shipped_languages_have_catalogues():
    assert set(SHIPPED) <= set(i18n.available())


@pytest.mark.parametrize("lang", SHIPPED)
def test_each_shipped_language_keeps_placeholders_and_has_its_plural_forms(lang):
    en, other = i18n.catalogue("en"), i18n.catalogue(lang)
    marks = lambda s: sorted(m for m in i18n._PARAM.findall(s) if m != "count")
    bad = [k for k, v in other.items() if marks(v) != marks(en.get(k) or en.get(PLURAL.sub("", k) + "_other", ""))]
    assert not bad, f"{lang}: placeholders differ from English in {bad[:10]}"
    want = FORMS.get(lang, {"one", "other"})
    stems = {PLURAL.sub("", k) for k in en if PLURAL.search(k)}
    missing = [f"{s}_{f}" for s in stems for f in want if f"{s}_{f}" not in other]
    assert not missing, f"{lang}: plural forms missing: {missing[:10]}"


def test_russian_plurals_pick_one_few_and_many():
    stem = next(PLURAL.sub("", k) for k, v in i18n.catalogue("en").items() if k.endswith("_other") and "{{count}}" in v
                and not i18n._PARAM.findall(v.replace("{{count}}", "")))
    ru = i18n.catalogue("ru")
    for n, form in ((1, "one"), (21, "one"), (3, "few"), (24, "few"), (5, "many"), (11, "many")):
        assert i18n.t(stem, "ru", count=n) == ru[f"{stem}_{form}"].replace("{{count}}", str(n))


def test_chinese_and_brazilian_portuguese_are_matched_from_browser_tags():
    assert i18n.match("zh-CN") == "zh-Hans" and i18n.match("zh") == "zh-Hans"
    assert i18n.match("zh-TW") is None and i18n.match("zh-Hant-HK") is None
    assert i18n.match("pt") == "pt-BR" and i18n.match("pt-PT") == "pt-BR"
    assert i18n.accept_language("zh-TW,zh;q=0.8,en;q=0.5") == "zh-Hans"


def test_automatic_follows_the_askers_browser_but_never_for_someone_else():
    me = SimpleNamespace(id=7, settings={})
    other = SimpleNamespace(id=8, settings={})
    chosen = SimpleNamespace(id=7, settings={"language": "fr"})
    token = i18n._request.set((7, "de-DE,de;q=0.9,en;q=0.5"))
    try:
        assert i18n.user_language(me) == "de"
        assert i18n.user_language(other) == "en"
        assert i18n.user_language(chosen) == "fr"
        assert i18n.user_language({"language": None}) == "en"
    finally:
        i18n._request.reset(token)
    assert i18n.user_language(me) == "en"


async def test_a_signed_in_request_renders_in_the_browsers_language(client, monkeypatch):
    from mailr.api import activity_routes

    seen = []

    async def feed(*_a, lang, **_k):
        seen.append(lang)
        return {"items": [], "next_cursor": None}

    monkeypatch.setattr(activity_routes.activity, "feed", feed)
    assert (await client.get("/api/v1/activity", headers={"Accept-Language": "de-DE,de;q=0.9"})).status_code == 200
    assert (await client.get("/api/v1/activity", headers={"Accept-Language": "zh-CN"})).status_code == 200
    assert (await client.get("/api/v1/activity")).status_code == 200
    assert seen == ["de", "zh-Hans", "en"]
