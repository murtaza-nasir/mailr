"""Language identification of message bodies (fastText lid.176), and the "Translate" offer.

At ingest the visible body text (mime.shown_text: the rendered HTML's text or the plain part,
quoted history and the signature left out) goes through fastText's lid.176 model, the
compressed 917 KB `lid.176.ftz` (Joulin et al., "Bag of Tricks for Efficient Text
Classification" and "FastText.zip"; CC BY-SA 3.0). Inference is local CPU work: no text
leaves the server, so local-only mail is no exception. The language (ISO 639-1 where one
exists) and the model's probability are stored on the message.

The reader offers "Translate" when the language is known with at least OFFER_CONFIDENCE and
is not one of the user's own languages (the About-you profile's `languages`, English when
none is set).

The model ships in the image (the Dockerfile downloads it with a pinned SHA-256). A development
checkout fetches it with `mailr langid-model`; without it, detection is off and nothing is
stored (`mailr langid-backfill` fills the gap later).
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from pathlib import Path

log = logging.getLogger("mailr.langid")

MODEL_URL = "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz"
MODEL_SHA256 = "8f3472cfe8738a7b6099e8e999c3cbfae0dcd15696aac7d7738a8039db603e83"
MODEL_PATH = Path(__file__).resolve().parent.parent / "data" / "lid.176.ftz"

MIN_LETTERS = 40  # shorter bodies ("Thanks!", "See attached") say too little: no language stored
MAX_CHARS = 3000  # the start of the body is enough, and keeps the cost flat
# lid.176's probability separates the cases well: Spanish, Vietnamese, Arabic, Urdu or Japanese
# mail of a sentence or two scores 0.93-1.0, while English made of headings, product names,
# jargon or loanwords (the case that used to misfire) scores 0.16-0.72 for English, and the
# wrong labels such text gets stay near 0.6 at most ("Bonjour Cafe menu: croissant..." is
# French at 0.58). 0.8 sits clear of those misreadings; a one-line foreign note can fall below
# it and gets no bar, but Translate stays in the message menu.
OFFER_CONFIDENCE = 0.8
DEFAULT_LANGUAGES = frozenset({"en"})

# lid.176 labels for regional varieties that have an ISO 639-1 macrolanguage code.
_MACRO = {"arz": "ar", "azb": "az", "pnb": "pa", "ckb": "ku", "yue": "zh", "wuu": "zh", "nb": "no"}

_model = None
_model_failed = False
_lock = threading.Lock()


def _load():
    global _model, _model_failed
    if _model is not None or _model_failed:
        return _model
    with _lock:
        if _model is None and not _model_failed:
            if not MODEL_PATH.is_file():
                _model_failed = True
                log.warning("language identification is off: %s is missing (run `mailr langid-model`)", MODEL_PATH)
                return None
            try:
                import fasttext  # fasttext-predict: inference only

                _model = fasttext.load_model(str(MODEL_PATH))
            except Exception as e:  # noqa: BLE001
                _model_failed = True
                log.warning("language identification is off: %s: %s", type(e).__name__, e)
    return _model


def available() -> bool:
    return _load() is not None


def normalize(code: str | None) -> str | None:
    """A lid.176 label as ISO 639-1 where one exists (Egyptian Arabic is Arabic, Cantonese is
    Chinese); other labels stay as they are."""
    if not code:
        return None
    code = code.strip().lower().replace("__label__", "")
    return _MACRO.get(code, code) or None


_SIG = re.compile(r"(?m)^-- ?$")  # the standard signature delimiter (RFC 3676)
_NOISE = re.compile(r"(?:https?://|www\.)\S+|[\w.+-]+@[\w-]+\.[\w.-]+|[\d_]+")
_LETTER = re.compile(r"[^\W\d_]")
# Chinese, Japanese and Korean characters carry about a word each: one counts as three letters
# toward MIN_LETTERS (a two-line Japanese note is as telling as a two-line English one).
_DENSE = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")
DENSE_WEIGHT = 3


def letter_count(text: str) -> int:
    return len(_LETTER.findall(text)) + (DENSE_WEIGHT - 1) * len(_DENSE.findall(text))


def language_text(text: str) -> str:
    """The text the model reads: no signature, links, addresses or numbers, one line (fastText
    reads one line per prediction), at most MAX_CHARS."""
    from mailr.core.mime import make_snippet

    t = text or ""
    m = _SIG.search(t)
    if m and m.start() > 0:
        t = t[: m.start()]
    t = make_snippet(t, MAX_CHARS * 2)
    t = _NOISE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()[:MAX_CHARS]


def detect(text: str) -> tuple[str | None, float | None]:
    """(language, probability) of a body text.

    (None, None): the model is not available (nothing known, a later backfill can fill it).
    (None, 0.0): checked, but there are fewer than MIN_LETTERS letters to go on."""
    model = _load()
    if model is None:
        return None, None
    t = language_text(text)
    if letter_count(t) < MIN_LETTERS:
        return None, 0.0
    labels, probs = model.predict(t, k=1)
    if not labels:
        return None, 0.0
    return normalize(labels[0]), round(min(1.0, max(0.0, float(probs[0]))), 4)


# --- The user's own languages -----------------------------------------------------------

# English names (and the most common own-language names) of the languages lid.176 knows, to
# ISO 639-1 codes (or the model's own code where there is none).
NAMES: dict[str, tuple[str, ...]] = {
    "afrikaans": ("af",), "albanian": ("sq",), "amharic": ("am",), "arabic": ("ar",), "aragonese": ("an",),
    "armenian": ("hy",), "assamese": ("as",), "asturian": ("ast",), "avaric": ("av",), "azerbaijani": ("az",),
    "azeri": ("az",), "bashkir": ("ba",), "basque": ("eu",), "belarusian": ("be",), "bengali": ("bn",),
    "bangla": ("bn",), "bihari": ("bh",), "bosnian": ("bs", "hr", "sr", "sh"), "breton": ("br",),
    "bulgarian": ("bg",), "burmese": ("my",), "catalan": ("ca",), "valencian": ("ca",), "cebuano": ("ceb",),
    "chechen": ("ce",), "chinese": ("zh",), "mandarin": ("zh",), "cantonese": ("zh",), "corsican": ("co",),
    "croatian": ("hr", "bs", "sr", "sh"), "czech": ("cs",), "danish": ("da",), "dhivehi": ("dv",),
    "maldivian": ("dv",), "dutch": ("nl",), "flemish": ("nl",), "english": ("en",), "esperanto": ("eo",),
    "estonian": ("et",), "farsi": ("fa",), "persian": ("fa",), "dari": ("fa",), "finnish": ("fi",),
    "french": ("fr",), "frisian": ("fy",), "galician": ("gl",), "georgian": ("ka",), "german": ("de",),
    "greek": ("el",), "guarani": ("gn",), "gujarati": ("gu",), "haitian": ("ht",), "creole": ("ht",),
    "hebrew": ("he",), "hindi": ("hi",), "hungarian": ("hu",), "icelandic": ("is",), "ido": ("io",),
    "ilocano": ("ilo",), "indonesian": ("id", "ms"), "interlingua": ("ia",), "irish": ("ga",),
    "italian": ("it",), "japanese": ("ja",), "javanese": ("jv",), "kannada": ("kn",), "kazakh": ("kk",),
    "khmer": ("km",), "cambodian": ("km",), "korean": ("ko",), "kurdish": ("ku",), "kyrgyz": ("ky",),
    "lao": ("lo",), "latin": ("la",), "latvian": ("lv",), "lithuanian": ("lt",), "luxembourgish": ("lb",),
    "macedonian": ("mk",), "malagasy": ("mg",), "malay": ("ms", "id"), "malayalam": ("ml",), "maltese": ("mt",),
    "manx": ("gv",), "marathi": ("mr",), "mongolian": ("mn",), "nepali": ("ne",), "norwegian": ("no", "nn"),
    "bokmal": ("no",), "nynorsk": ("nn", "no"), "occitan": ("oc",), "oriya": ("or",), "odia": ("or",),
    "ossetian": ("os",), "pashto": ("ps",), "pushto": ("ps",), "polish": ("pl",), "portuguese": ("pt",),
    "punjabi": ("pa",), "panjabi": ("pa",), "quechua": ("qu",), "romanian": ("ro",), "moldovan": ("ro",),
    "romansh": ("rm",), "russian": ("ru",), "sanskrit": ("sa",), "sardinian": ("sc",), "gaelic": ("gd",),
    "serbian": ("sr", "hr", "bs", "sh"), "serbo-croatian": ("sh", "sr", "hr", "bs"), "sindhi": ("sd",),
    "sinhala": ("si",), "sinhalese": ("si",), "slovak": ("sk",), "slovenian": ("sl",), "slovene": ("sl",),
    "somali": ("so",), "spanish": ("es",), "castilian": ("es",), "sundanese": ("su",), "swahili": ("sw",),
    "kiswahili": ("sw",), "swedish": ("sv",), "tagalog": ("tl",), "filipino": ("tl",), "tajik": ("tg",),
    "tamil": ("ta",), "tatar": ("tt",), "telugu": ("te",), "thai": ("th",), "tibetan": ("bo",),
    "turkish": ("tr",), "turkmen": ("tk",), "ukrainian": ("uk",), "urdu": ("ur",), "uyghur": ("ug",),
    "uighur": ("ug",), "uzbek": ("uz",), "vietnamese": ("vi",), "walloon": ("wa",), "welsh": ("cy",),
    "yiddish": ("yi",), "yoruba": ("yo",), "scots": ("sco",), "waray": ("war",),
    # Names in the language itself.
    "español": ("es",), "castellano": ("es",), "français": ("fr",), "deutsch": ("de",), "italiano": ("it",),
    "português": ("pt",), "nederlands": ("nl",), "polski": ("pl",), "svenska": ("sv",), "norsk": ("no", "nn"),
    "dansk": ("da",), "suomi": ("fi",), "türkçe": ("tr",), "tiếng việt": ("vi",), "bahasa": ("id", "ms"),
    "русский": ("ru",), "українська": ("uk",), "ελληνικά": ("el",), "עברית": ("he",), "العربية": ("ar",),
    "عربي": ("ar",), "اردو": ("ur",), "فارسی": ("fa",), "हिन्दी": ("hi",), "हिंदी": ("hi",), "বাংলা": ("bn",),
    "ਪੰਜਾਬੀ": ("pa",), "中文": ("zh",), "普通话": ("zh",), "日本語": ("ja",), "한국어": ("ko",), "ไทย": ("th",),
}
# Codes typed on their own ("en, ur") count as well.
_CODES = frozenset(c for codes in NAMES.values() for c in codes) | frozenset(_MACRO)
_WORD = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*")


def parse_languages(value: str | None) -> frozenset[str]:
    """The language codes in a free-text list such as "English (native), Urdu; some Arabic".
    Empty when nothing is recognised."""
    found: set[str] = set()
    for item in re.split(r"[,;/\n&+|]|\band\b", (value or "").lower()):
        item = item.strip(" .()[]-")
        if not item:
            continue
        if item in _CODES:
            found.add(normalize(item) or item)
            continue
        if item in NAMES:
            found.update(NAMES[item])
            continue
        words = _WORD.findall(item)
        for i, w in enumerate(words):
            pair = f"{w} {words[i + 1]}" if i + 1 < len(words) else ""
            if pair in NAMES:
                found.update(NAMES[pair])
            elif w in NAMES:
                found.update(NAMES[w])
    return frozenset(found)


def own_languages_from(profile_languages: str | None) -> frozenset[str]:
    """The user's languages; English when the profile names none mailr recognises."""
    return parse_languages(profile_languages) or DEFAULT_LANGUAGES


async def own_languages(db, user_id: int) -> frozenset[str]:
    from mailr.models import AssistantProfile

    row = await db.get(AssistantProfile, user_id)
    return own_languages_from((row.data or {}).get("languages") if row is not None else None)


def offer_translate(lang: str | None, confidence: float | None, own: frozenset[str] | None = None) -> bool:
    """Whether the reader offers "Translate": a language the model is sure of that is not one
    of the user's own."""
    code = normalize(lang)
    if not code or confidence is None or confidence < OFFER_CONFIDENCE:
        return False
    return code not in (own or DEFAULT_LANGUAGES)


# --- The model file ---------------------------------------------------------------------


def fetch_model(path: Path = MODEL_PATH) -> Path:
    """Download lid.176.ftz and check its SHA-256 (a development checkout; the image has it)."""
    import httpx

    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == MODEL_SHA256:
        return path
    r = httpx.get(MODEL_URL, follow_redirects=True, timeout=60)
    r.raise_for_status()
    digest = hashlib.sha256(r.content).hexdigest()
    if digest != MODEL_SHA256:
        raise RuntimeError(f"lid.176.ftz checksum mismatch: {digest}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(r.content)
    tmp.replace(path)
    global _model_failed
    _model_failed = False
    return path
