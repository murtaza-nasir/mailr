"""Texts the server writes itself, in the reader's language.

The catalogue is one JSON file per language in this folder (`en.json`, later `de.json`, ...), nested
by area, the same shape as the web's `frontend/src/locales/*.json`:

- `{{name}}` fills a parameter, as in Speakr and on the web.
- A count picks a plural form by the language's rules: `key_one`, `key_other` (and `key_zero`,
  `key_two`, `key_few`, `key_many` where a language has them), chosen by the `count` parameter.
- English is the fallback for a key another language lacks. A key that English lacks too comes
  back as the key itself (the test suite makes sure no code asks for one).

Who reads in which language:

- API errors (`errors.*`): `detail` stays English for older apps, and the response also carries
  `code` and `params` (`ApiError`, below) so each app shows the text in its own language. The apps
  fetch the `errors` section from GET /api/v1/i18n/messages.
- Push notifications, the bell and Activity, invite mail and digests render in the recipient's
  language: the account setting `language` (Settings → You → Region), else English.
- Ambrose answers in the account's language (agent/context.py); its prompts stay English.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from fastapi import HTTPException

log = logging.getLogger(__name__)

DEFAULT = "en"
HERE = Path(__file__).parent
_TAG = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
_PARAM = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_PLURAL_FORMS = ("zero", "one", "two", "few", "many", "other")
# Values of the setting that mean "follow the device" (the web's Automatic).
FOLLOW = {"", "auto", "system", "device"}

_cache: dict[str, tuple[float, dict[str, str]]] = {}
_lock = threading.Lock()
_missing_logged: set[str] = set()


def _flatten(d: Mapping[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, Mapping):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = str(v)
    return out


def available() -> list[str]:
    """The languages the catalogue has, English first."""
    codes = sorted(p.stem for p in HERE.glob("*.json") if _TAG.match(p.stem))
    return [DEFAULT, *[c for c in codes if c != DEFAULT]]


def catalogue(lang: str) -> dict[str, str]:
    """The flattened catalogue of `lang` (key → text), read again when its file changes; {} when absent."""
    if not _TAG.match(lang or ""):
        return {}
    path = HERE / f"{lang}.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    hit = _cache.get(lang)
    if hit and hit[0] == mtime:
        return hit[1]
    with _lock:
        try:
            data = _flatten(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            log.error("i18n catalogue %s could not be read: %s", path, e)
            return {}
        _cache[lang] = (mtime, data)
        return data


# Traditional Chinese (Taiwan, Hong Kong, Macau) is not given Simplified Chinese: it falls back.
_TRADITIONAL = re.compile(r"^zh-(hant|tw|hk|mo)\b")


def match(tag: str | None, langs: list[str] | None = None) -> str | None:
    """The catalogue language closest to `tag` ("pt-BR", then "pt", then "pt-…"), or None."""
    if not tag or tag.strip().lower() in FOLLOW:
        return None
    t = tag.strip().replace("_", "-").lower()
    langs = langs if langs is not None else available()
    for c in langs:
        if c.lower() == t:
            return c
    if _TRADITIONAL.match(t):
        return None
    base = t.split("-")[0]
    for c in langs:
        if c.lower() == base:
            return c
    for c in langs:
        if c.split("-")[0].lower() == base:
            return c
    return None


def clean_language(v: Any) -> str | None:
    """The `language` setting checked (ValueError): a BCP 47 tag such as "de" or "pt-BR", or None
    for Automatic. A language the server has no catalogue for is allowed; it reads as English here."""
    if v is None or (isinstance(v, str) and v.strip().lower() in FOLLOW):
        return None
    if not isinstance(v, str) or not _TAG.match(v.strip()) or len(v) > 35:
        raise ValueError("language must be a language tag such as en, de or pt-BR")
    return v.strip()


# The signed-in person of this request and their browser's Accept-Language (set in core/auth.py).
_request: ContextVar[tuple[Any, str | None] | None] = ContextVar("mailr_i18n_request", default=None)


def set_request_language(user_id: Any, header: str | None) -> None:
    """Remember who asks in this request and the languages their browser accepts."""
    _request.set((user_id, header))


def user_language(user_or_settings: Any) -> str:
    """The catalogue language for a person: their `language` setting; with the setting on Automatic,
    their browser's Accept-Language while they themselves make the request; else English.

    A text rendered for someone else (a push to another member) never takes the asker's browser
    language: it is matched by user id."""
    settings = user_or_settings if isinstance(user_or_settings, Mapping) else getattr(user_or_settings, "settings", None)
    chosen = match((settings or {}).get("language"))
    if chosen:
        return chosen
    req = _request.get()
    uid = getattr(user_or_settings, "id", None)
    if req and uid is not None and req[0] == uid and not chosen_language(user_or_settings):
        return accept_language(req[1]) or DEFAULT
    return DEFAULT


# English names of languages, for Ambrose's prompt (which stays English); a tag not listed reads as itself.
LANGUAGE_NAMES = {
    "ar": "Arabic", "bg": "Bulgarian", "ca": "Catalan", "cs": "Czech", "cy": "Welsh", "da": "Danish",
    "de": "German", "el": "Greek", "en": "English", "es": "Spanish", "et": "Estonian", "fa": "Persian",
    "fi": "Finnish", "fr": "French", "ga": "Irish", "he": "Hebrew", "hi": "Hindi", "hr": "Croatian",
    "hu": "Hungarian", "id": "Indonesian", "is": "Icelandic", "it": "Italian", "ja": "Japanese", "ko": "Korean",
    "lt": "Lithuanian", "lv": "Latvian", "ms": "Malay", "nb": "Norwegian Bokmål", "nl": "Dutch", "no": "Norwegian",
    "pl": "Polish", "pt": "Portuguese", "pt-BR": "Brazilian Portuguese", "pt-PT": "European Portuguese",
    "ro": "Romanian", "ru": "Russian", "sk": "Slovak", "sl": "Slovenian", "sr": "Serbian", "sv": "Swedish",
    "sw": "Swahili", "th": "Thai", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu", "vi": "Vietnamese",
    "zh": "Chinese", "zh-Hans": "Simplified Chinese", "zh-Hant": "Traditional Chinese",
}


def language_name(tag: str) -> str:
    """The English name of a language tag ("de" → "German"), or the tag itself."""
    return LANGUAGE_NAMES.get(tag) or LANGUAGE_NAMES.get(tag.split("-")[0]) or tag


def chosen_language(user_or_settings: Any) -> str | None:
    """The person's `language` setting as a tag (any language, not only those with a catalogue), or
    None for Automatic or a value that is no language tag."""
    settings = user_or_settings if isinstance(user_or_settings, Mapping) else getattr(user_or_settings, "settings", None)
    try:
        return clean_language((settings or {}).get("language"))
    except ValueError:
        return None


def accept_language(header: str | None) -> str | None:
    """The best catalogue language for an Accept-Language header, or None."""
    if not header:
        return None
    ranked = []
    for i, part in enumerate(header.split(",")):
        tag, _, q = part.strip().partition(";q=")
        try:
            weight = float(q) if q else 1.0
        except ValueError:
            weight = 0.0
        if tag and tag != "*" and weight > 0:
            ranked.append((-weight, i, tag))
    for _w, _i, tag in sorted(ranked):
        got = match(tag)
        if got:
            return got
    return None


def plural_category(lang: str, n: float) -> str:
    """The CLDR cardinal category of `n` in `lang` (the forms the catalogue's `_one`/`_other`... keys use).

    The common European rules; a language not listed takes English's one/other."""
    base = (lang or DEFAULT).split("-")[0].lower()
    i = int(abs(n)) if float(n).is_integer() else None
    if base in ("ja", "zh", "ko", "vi", "th", "id", "ms", "tr"):
        return "other"
    if base in ("fr", "pt") and lang.lower() != "pt-pt":
        return "one" if i in (0, 1) else "other"
    if base in ("ru", "uk", "be", "sr", "hr", "bs") and i is not None:
        if i % 10 == 1 and i % 100 != 11:
            return "one"
        if 2 <= i % 10 <= 4 and not 12 <= i % 100 <= 14:
            return "few"
        return "many"
    if base == "pl" and i is not None:
        if i == 1:
            return "one"
        if 2 <= i % 10 <= 4 and not 12 <= i % 100 <= 14:
            return "few"
        return "many"
    if base in ("cs", "sk") and i is not None:
        return "one" if i == 1 else "few" if 2 <= i <= 4 else "other"
    if base == "ar" and i is not None:
        if i == 0:
            return "zero"
        if i == 1:
            return "one"
        if i == 2:
            return "two"
        if 3 <= i % 100 <= 10:
            return "few"
        if 11 <= i % 100 <= 99:
            return "many"
        return "other"
    return "one" if i == 1 else "other"


def interpolate(text: str, params: Mapping[str, Any]) -> str:
    """`{{name}}` filled from params; a name without a value stays as written."""
    return _PARAM.sub(lambda m: str(params[m[1]]) if m[1] in params and params[m[1]] is not None else m[0], text)


def has(key: str) -> bool:
    """Whether English has `key` (as itself or as plural forms)."""
    en = catalogue(DEFAULT)
    return key in en or f"{key}_other" in en


def t(key: str, lang: str | None = None, /, **params: Any) -> str:
    """The text of `key` in `lang` (English when absent), with `params` filled in.

    With a `count` parameter, the plural form for that count is used when the key has forms."""
    lang = lang or DEFAULT
    chain = [lang] if lang == DEFAULT else [lang, DEFAULT]
    count = params.get("count")
    for code in chain:
        table = catalogue(code)
        text = None
        if isinstance(count, (int, float)) and not isinstance(count, bool):
            text = table.get(f"{key}_{plural_category(code, count)}") or table.get(f"{key}_other")
        if text is None:
            text = table.get(key)
        if text is not None:
            return interpolate(text, params)
    if key not in _missing_logged:
        _missing_logged.add(key)
        log.warning("i18n: no text for %s", key)
    return key


def msg(key: str, /, **params: Any) -> dict[str, Any]:
    """A text kept with the data and rendered when it is read, in the reader's language:
    `{"key": ..., "params": {...}}`. A parameter may itself be a msg (a quoted title, "3
    conversations"); it is rendered first. Activity entries, approval cards and health lines keep
    their texts this way."""
    return {"key": key, "params": {k: v for k, v in params.items() if v is not None}}


def is_msg(v: Any) -> bool:
    return isinstance(v, Mapping) and isinstance(v.get("key"), str)


def render(m: Any, lang: str | None = None, fallback: str | None = None) -> str:
    """A msg (see `msg`) as text in `lang`. Not a msg, or a key the catalogue lacks: `fallback`
    (the English text stored beside it), else the key."""
    if not is_msg(m):
        return fallback if fallback is not None else ("" if m is None else str(m))
    key = m["key"]
    if not has(key):
        return fallback if fallback is not None else key
    raw = m.get("params") if isinstance(m.get("params"), Mapping) else {}
    params = {k: (render(v, lang) if is_msg(v) else v) for k, v in raw.items()}
    return t(key, lang, **params)


def render_params(params: Mapping[str, Any] | None, lang: str | None = None) -> dict[str, Any]:
    """`params` with every msg among them rendered (for a client that fills a key of its own)."""
    return {k: (render(v, lang) if is_msg(v) else v) for k, v in (params or {}).items()}


class Text(str):
    """English text that remembers its msg (`text()`): it compares and prints as the English, and a
    reader with another language renders `.msg` (`msg_of`)."""

    msg: dict[str, Any]


def text(key: str, /, **params: Any) -> Text:
    """A msg rendered in English, as a str that keeps the msg (`Text`)."""
    m = msg(key, **params)
    out = Text(render(m))
    out.msg = m
    return out


def msg_of(v: Any) -> dict[str, Any] | None:
    """The msg of a `Text` or a msg, else None."""
    if isinstance(v, Text):
        return v.msg
    return v if is_msg(v) else None


def day_text(d: Any, lang: str | None = None) -> str:
    """A date as mail says it ("Tue 6 Oct 2026" in English), with the language's names and order
    (mail.date.*): the server has no locale library, so the catalogue holds the names."""
    return t("mail.date.day", lang, weekday=t(f"mail.date.weekday.{d.weekday()}", lang), day=d.day,
             month=t(f"mail.date.month.{d.month}", lang), year=d.year)


def time_text(dt: Any, lang: str | None = None) -> str:
    """A time of day as mail says it ("14:00")."""
    return t("mail.date.time", lang, hour=f"{dt.hour:02d}", minute=f"{dt.minute:02d}")


def section(prefix: str, lang: str) -> dict[str, str]:
    """Every key under `prefix.` in `lang`, English filling the gaps, without the prefix."""
    p = prefix.rstrip(".") + "."
    out = {k[len(p):]: v for k, v in catalogue(DEFAULT).items() if k.startswith(p)}
    if lang != DEFAULT:
        out.update({k[len(p):]: v for k, v in catalogue(lang).items() if k.startswith(p)})
    return out


class ApiError(HTTPException):
    """An API error with a stable code: `{"detail": English text, "code": ..., "params": {...}}`.

    `code` is a key under `errors.` in the catalogue; `detail` is its English text, exactly what the
    API said before codes existed, so older apps keep working. `params` are the values the text
    fills in; apps translate with them."""

    def __init__(self, status_code: int, code: str, /, headers: dict[str, str] | None = None,
                 fallback: str | None = None, **params: Any) -> None:
        """`fallback` is the English text when the catalogue has no `errors.<code>` (a code built from
        a value, such as `common.{kind}_not_found` for a kind with no text yet)."""
        self.code = code
        self.params = {k: v for k, v in params.items() if v is not None}
        key = f"errors.{code}"
        detail = t(key, DEFAULT, **self.params) if fallback is None or has(key) else fallback
        super().__init__(status_code, detail, headers=headers)

    def body(self) -> dict[str, Any]:
        return {"detail": self.detail, "code": self.code, "params": self.params}
