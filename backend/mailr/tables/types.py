"""Column types: checking a typed value, reading text into a value, the plain text of a value, and
converting a column to another type.

Values as stored in a row (JSON):

    text          "a string"
    number        "82.5"            a decimal string, never a float
    currency      "12.40"           as number; the currency is the column's
    date          "2026-10-05"      a date, never shifted by zones
    datetime      {"at": "2026-10-05T14:30", "tz": "America/Chicago"}
    checkbox      true              (unchecked is an empty cell)
    select        "o3k9x2"          an option id of the column
    multi_select  ["o3k9x2", …]     option ids
    link          [{"kind": "row", "table": "t…", "row": "<uuid>"}, …]
                  {"kind": "conversation", "conversation_id": 123} | {"kind": "event", "uid", "occurrence"?}
                  | {"kind": "task", "uid"} | {"kind": "note", "id": "n…" | "t…"}

An empty value (None, "", [], false) is an absent cell. `check` raises CellError for a value
that does not fit, naming the column and the type expected. `parse_text` reads typed or pasted
text and returns (value, None), or (None, text) when it does not convert: the caller keeps that
text in the row's `raw`.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

TYPES = ("text", "number", "currency", "date", "datetime", "checkbox", "select", "multi_select", "link")
LINK_TARGETS = ("table", "conversation", "event", "task", "note")
LIMITS = {"columns": 50, "rows": 10_000, "cell": 10_000, "options": 200, "multi": 50, "views": 20,
          "name": 200, "description": 2_000, "column_name": 100, "view_name": 100, "csv_bytes": 5 * 1024 * 1024}
TYPE_NAMES = {"text": "text", "number": "a number", "currency": "an amount", "date": "a date (YYYY-MM-DD)",
              "datetime": "a date and time", "checkbox": "true or false", "select": "one of the column's options",
              "multi_select": "a list of the column's options", "link": "a list of links"}
LABELS = {"text": "Text", "number": "Number", "currency": "Currency", "date": "Date", "datetime": "Date and time",
          "checkbox": "Checkbox", "select": "Select", "multi_select": "Multi-select", "link": "Link"}
OPTION_COLORS = ("gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red")
MAX_ABS = Decimal("1e15")
DETECT_SHARE = 0.6  # a CSV column takes a type when this share of its values convert
MAX_PLACES = 10
_ID = "23456789abcdefghjkmnpqrstuvwxyz"


def new_id(prefix: str, n: int = 6) -> str:
    return prefix + "".join(secrets.choice(_ID) for _ in range(n))


class CellError(ValueError):
    """A value that does not fit its column: the message names the column and the type."""

    def __init__(self, column: str, ctype: str, got: Any = None, detail: str | None = None):
        self.column, self.ctype = column, ctype
        msg = detail or f"Column {column!r} takes {TYPE_NAMES.get(ctype, ctype)}"
        if got is not None and detail is None:
            msg += f", not {str(got)[:60]!r}"
        super().__init__(msg)


@dataclass
class Col:
    """What the type functions need of a column."""
    col_id: str
    name: str
    type: str
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def options(self) -> list[dict[str, Any]]:
        return list((self.config or {}).get("options") or [])


@dataclass
class Ctx:
    """The reader's settings for text: today, the date order and the user's zone and currency."""
    today: date = field(default_factory=date.today)
    order: str = "mdy"
    tz: str = "UTC"
    currency: str = "USD"


def col_of(c: Any) -> Col:
    return c if isinstance(c, Col) else Col(c.col_id, c.name, c.type, dict(c.config or {}))


# ---------------------------------------------------------------- numbers


def norm_decimal(d: Decimal) -> str:
    """A plain decimal string: no exponent, no trailing zeros after the point."""
    if not d.is_finite():
        raise InvalidOperation
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s


def to_decimal(v: Any) -> Decimal:
    if isinstance(v, bool):
        raise InvalidOperation
    if isinstance(v, float):
        v = repr(v)
    d = Decimal(str(v).strip())
    if not d.is_finite() or abs(d) >= MAX_ABS:
        raise InvalidOperation
    if d.as_tuple().exponent < -MAX_PLACES:  # type: ignore[operator]
        d = d.quantize(Decimal(1).scaleb(-MAX_PLACES))
    return d


_NUM_TEXT = re.compile(r"^\(?[-+−]?\s*[^\d\s.,()-]{0,4}\s*[-+−]?[\d.,'\s ]*\d[\d.,'\s ]*\s*[^\d\s.,()]{0,4}\)?%?$")


def parse_number(text: str) -> tuple[Decimal | None, str | None]:
    """A number from text: currency symbols and codes, thousands separators (`,` `'` spaces)
    and `(12)` for negatives are read. Returns (number, symbol or code seen)."""
    t = text.strip().replace("−", "-").replace(" ", " ")
    if not t or not _NUM_TEXT.match(t):
        return None, None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    sym = "".join(ch for ch in t if not (ch.isdigit() or ch in ".,'- +%")).strip() or None
    pct = t.endswith("%")
    core = "".join(ch for ch in t if ch.isdigit() or ch in ".,-+")
    if core.count("-") + core.count("+") > 1:
        return None, None
    sign = -1 if "-" in core else 1
    core = core.replace("-", "").replace("+", "")
    if "," in core and "." in core:
        # The later separator is the decimal point.
        if core.rfind(",") > core.rfind("."):
            core = core.replace(".", "").replace(",", ".")
        else:
            core = core.replace(",", "")
    elif "," in core:
        parts = core.split(",")
        if len(parts) == 2 and len(parts[1]) != 3:
            core = core.replace(",", ".")  # 82,5 is a decimal comma
        else:
            core = core.replace(",", "")
    if core.count(".") > 1:
        core = core.replace(".", "")
    try:
        d = Decimal(core)
    except InvalidOperation:
        return None, None
    if pct:
        d = d / 100
    d = d * sign * (-1 if neg else 1)
    return d, sym


CURRENCY_SYMBOLS = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "C$": "CAD",
                    "A$": "AUD", "CHF": "CHF", "₩": "KRW", "R$": "BRL", "₽": "RUB", "kr": "SEK", "zł": "PLN",
                    "₺": "TRY", "₪": "ILS", "Rs": "PKR", "Rs.": "PKR", "₨": "PKR"}
CURRENCY_CODES = ("USD", "EUR", "GBP", "JPY", "INR", "CAD", "AUD", "CHF", "CNY", "KRW", "BRL", "MXN", "SEK", "NOK",
                  "DKK", "PLN", "TRY", "ILS", "PKR", "NZD", "SGD", "HKD", "ZAR", "AED", "SAR", "RUB", "CZK", "HUF")


def currency_of(sym: str | None) -> str | None:
    if not sym:
        return None
    s = sym.strip()
    if s.upper() in CURRENCY_CODES:
        return s.upper()
    return CURRENCY_SYMBOLS.get(s)


# ---------------------------------------------------------------- dates

MONTHS = {m: i + 1 for i, ms in enumerate((
    ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
    ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
    ("dec", "december"))) for m in ms}
WEEKDAYS = {w: i for i, ws in enumerate((("mon", "monday"), ("tue", "tues", "tuesday"), ("wed", "weds", "wednesday"),
                                          ("thu", "thur", "thurs", "thursday"), ("fri", "friday"),
                                          ("sat", "saturday"), ("sun", "sunday"))) for w in ws}
RELATIVE = {"today": 0, "tod": 0, "now": 0, "tomorrow": 1, "tom": 1, "tmr": 1, "tmrw": 1, "yesterday": -1, "yest": -1}


def _ymd(y: int, m: int, d: int) -> date | None:
    try:
        if y < 100:
            y += 2000
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(text: str, ctx: Ctx | None = None) -> date | None:
    """A date from text: ISO dates, numeric dates in the date-order setting (`12/10/2026`), month
    names (`12 oct`, `Oct 12, 2026`) and the quick-add words (today, tom, yesterday, fri: the next
    Friday after today, as the typed date field of the when picker reads it)."""
    ctx = ctx or Ctx()
    t = text.strip().lower().rstrip(".")
    if not t:
        return None
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[t ][\d:.]+(?:z|[+-]\d{2}:?\d{2})?)?", t)
    if m:
        return _ymd(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{2}|\d{4}))?", t)
    if m:
        a, b = int(m[1]), int(m[2])
        mo, d = (b, a) if ctx.order == "dmy" else (a, b)
        y = int(m[3]) if m[3] else ctx.today.year
        return _ymd(y, mo, d)
    if t in RELATIVE:
        return ctx.today + timedelta(days=RELATIVE[t])
    if t in WEEKDAYS:
        delta = (WEEKDAYS[t] - ctx.today.weekday()) % 7 or 7
        return ctx.today + timedelta(days=delta)
    words = re.findall(r"[a-z]+|\d+", t)
    nums = [int(w) for w in words if w.isdigit()]
    mons = [MONTHS[w] for w in words if w in MONTHS]
    rest = [w for w in words if not w.isdigit() and w not in MONTHS and w not in ("st", "nd", "rd", "th", "of")
            and w not in WEEKDAYS]
    if len(mons) == 1 and 1 <= len(nums) <= 2 and not rest:
        d = nums[0]
        y = nums[1] if len(nums) == 2 else ctx.today.year
        if d > 31 and len(nums) == 2:
            d, y = nums[1], nums[0]
        return _ymd(y, mons[0], d)
    return None


def parse_datetime(text: str, ctx: Ctx | None = None) -> dict[str, Any] | None:
    """A date with an optional time ("2026-10-05 14:30", "fri 3pm", "12 oct 9:15"), in the user's zone."""
    ctx = ctx or Ctx()
    t = text.strip()
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[t ](\d{1,2}):(\d{2})(?::\d{2})?(?:\.\d+)?(z|[+-]\d{2}:?\d{2})?", t.lower())
    if m:
        d = parse_date(m[1], ctx)
        if d is None or int(m[2]) > 23:
            return None
        return {"at": f"{d.isoformat()}T{int(m[2]):02d}:{m[3]}", "tz": "UTC" if m[4] == "z" else ctx.tz}
    tm = re.search(r"(?:^|\s)(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?$", t.lower())
    hh, mm = 0, 0
    datepart = t
    if tm and (tm[2] or tm[3]):
        hh, mm = int(tm[1]), int(tm[2] or 0)
        if tm[3] in ("pm", "p") and hh < 12:
            hh += 12
        if tm[3] in ("am", "a") and hh == 12:
            hh = 0
        if hh > 23 or mm > 59:
            return None
        datepart = t[:tm.start()].strip()
    d = parse_date(datepart, ctx)
    if d is None:
        return None
    return {"at": f"{d.isoformat()}T{hh:02d}:{mm:02d}", "tz": ctx.tz}


TRUE_WORDS = {"yes", "y", "true", "t", "1", "x", "✓", "✔", "checked", "done", "on"}
FALSE_WORDS = {"no", "n", "false", "f", "0", "", "unchecked", "off", "-"}


def parse_bool(text: str) -> bool | None:
    t = text.strip().lower()
    if t in TRUE_WORDS:
        return True
    if t in FALSE_WORDS:
        return False
    return None


# ---------------------------------------------------------------- checking values


def _zone_ok(tz: str) -> bool:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(tz)
        return True
    except Exception:  # noqa: BLE001
        return False


def is_empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v is False or v == {}


def ref_key(ref: dict[str, Any]) -> str:
    """The lookup key of a link reference ("row:<uuid>", "conversation:12", "event:<uid>")."""
    k = ref.get("kind")
    if k == "row":
        return f"row:{ref.get('row')}"
    if k == "conversation":
        return f"conversation:{ref.get('conversation_id')}"
    if k in ("event", "task"):
        return f"{k}:{ref.get('uid')}"
    return f"note:{ref.get('id')}"


def norm_ref(col: Col, r: Any) -> dict[str, Any]:
    """A link reference in its stored shape, for the column's target kind."""
    target = (col.config or {}).get("target")
    if not isinstance(r, dict):
        raise CellError(col.name, "link", r)
    kind = r.get("kind") or ("row" if target == "table" else target)
    want = "row" if target == "table" else target
    if kind != want:
        raise CellError(col.name, "link", detail=f"Column {col.name!r} links to {target}s, not {kind}s")
    if kind == "row":
        tbl, row = str(r.get("table") or (col.config or {}).get("table") or ""), str(r.get("row") or "")
        if not tbl.startswith("t") or not re.fullmatch(r"[0-9a-f-]{36}", row):
            raise CellError(col.name, "link", detail=f"Column {col.name!r} takes rows as {{table, row}}")
        if (col.config or {}).get("table") and tbl != col.config["table"]:
            raise CellError(col.name, "link", detail=f"Column {col.name!r} links to rows of another table")
        return {"kind": "row", "table": tbl, "row": row}
    if kind == "conversation":
        try:
            cid = int(r.get("conversation_id") if r.get("conversation_id") is not None else r.get("id"))
        except (TypeError, ValueError):
            raise CellError(col.name, "link", detail=f"Column {col.name!r} takes conversations by conversation_id") from None
        return {"kind": "conversation", "conversation_id": cid}
    if kind in ("event", "task"):
        uid = str(r.get("uid") or "")[:300]
        if not uid:
            raise CellError(col.name, "link", detail=f"Column {col.name!r} takes {kind}s by uid")
        out = {"kind": kind, "uid": uid}
        if kind == "event" and r.get("occurrence"):
            out["occurrence"] = str(r["occurrence"])[:64]
        return out
    pid = str(r.get("id") or "")
    if not re.fullmatch(r"[nt][0-9a-z]{6,15}", pid):
        raise CellError(col.name, "link", detail=f"Column {col.name!r} takes notes and tables by id")
    return {"kind": "note", "id": pid}


def check(col: Col, v: Any) -> Any:
    """The value to store for `v` in column `col` (None: an empty cell). Raises CellError."""
    if is_empty(v):
        return None
    t = col.type
    if t == "text":
        if not isinstance(v, str):
            if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
                v = str(v)
            else:
                raise CellError(col.name, t, v)
        if len(v) > LIMITS["cell"]:
            raise CellError(col.name, t, detail=f"A cell can hold at most {LIMITS['cell']:,} characters")
        return v
    if t in ("number", "currency"):
        try:
            return norm_decimal(to_decimal(v))
        except (InvalidOperation, ValueError, TypeError):
            raise CellError(col.name, t, v) from None
    if t == "date":
        if isinstance(v, date) and not isinstance(v, datetime):
            return v.isoformat()
        if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) or _ymd(*map(int, v.split("-"))) is None:
            raise CellError(col.name, t, v)
        return v
    if t == "datetime":
        if isinstance(v, str):
            v = {"at": v}
        if not isinstance(v, dict):
            raise CellError(col.name, t, v)
        at, tz = str(v.get("at") or ""), str(v.get("tz") or "UTC")
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::\d{2})?", at)
        if not m or _ymd(*map(int, m[1].split("-"))) is None or int(m[2]) > 23 or int(m[3]) > 59:
            raise CellError(col.name, t, v.get("at"))
        if not _zone_ok(tz):
            raise CellError(col.name, t, detail=f"Unknown time zone {tz!r}")
        return {"at": f"{m[1]}T{m[2]}:{m[3]}", "tz": tz}
    if t == "checkbox":
        if isinstance(v, bool):
            return True if v else None
        raise CellError(col.name, t, v)
    if t == "select":
        ids = {o["id"] for o in col.options}
        if not isinstance(v, str) or v not in ids:
            raise CellError(col.name, t, v)
        return v
    if t == "multi_select":
        if isinstance(v, str):
            v = [v]
        ids = [o["id"] for o in col.options]
        if not isinstance(v, list) or any(x not in ids for x in v):
            raise CellError(col.name, t, v)
        if len(v) > LIMITS["multi"]:
            raise CellError(col.name, t, detail=f"A cell can hold at most {LIMITS['multi']} values")
        seen = set(v)
        return [x for x in ids if x in seen] or None  # option order
    if t == "link":
        if isinstance(v, dict):
            v = [v]
        if not isinstance(v, list):
            raise CellError(col.name, t, v)
        refs: list[dict[str, Any]] = []
        keys: set[str] = set()
        for r in v:
            nr = norm_ref(col, r)
            k = ref_key(nr)
            if k not in keys:
                keys.add(k)
                refs.append(nr)
        if len(refs) > 1 and not (col.config or {}).get("multiple"):
            raise CellError(col.name, t, detail=f"Column {col.name!r} takes one link (it is not set to several)")
        if len(refs) > LIMITS["multi"]:
            raise CellError(col.name, t, detail=f"A cell can hold at most {LIMITS['multi']} links")
        return refs or None
    raise CellError(col.name, t, detail=f"unknown column type {t!r}")


# ---------------------------------------------------------------- text in and out


def option_by_name(col: Col, name: str) -> dict[str, Any] | None:
    n = name.strip().lower()
    return next((o for o in col.options if o["name"].strip().lower() == n), None)


def parse_text(col: Col, text: Any, ctx: Ctx | None = None, *, new_options: list[dict[str, Any]] | None = None
               ) -> tuple[Any, str | None]:
    """Typed, pasted or imported text in `col`: (value, None), or (None, text) when it does not
    convert. Select names that are not options become new options when `new_options` is a list
    (they are appended to it and to the column's config); otherwise they do not convert."""
    ctx = ctx or Ctx()
    if text is None:
        return None, None
    if not isinstance(text, str):
        try:
            return check(col, text), None
        except CellError:
            return None, str(text)
    s = text.strip()
    if not s:
        return None, None
    t = col.type
    if t == "text":
        return (s if "\n" not in text else text.strip("\n"))[:LIMITS["cell"]], None
    if t in ("number", "currency"):
        d, _sym = parse_number(s)
        if d is None or abs(d) >= MAX_ABS:
            return None, s
        return norm_decimal(d), None
    if t == "date":
        d = parse_date(s, ctx)
        return (d.isoformat(), None) if d else (None, s)
    if t == "datetime":
        v = parse_datetime(s, ctx)
        return (v, None) if v else (None, s)
    if t == "checkbox":
        b = parse_bool(s)
        return (True if b else None, None) if b is not None else (None, s)
    if t == "select":
        o = option_by_name(col, s) or next((o for o in col.options if o["id"] == s), None)
        if o is None and new_options is not None:
            o = add_option(col, s)
            new_options.append(o)
        return (o["id"], None) if o else (None, s)
    if t == "multi_select":
        ids: list[str] = []
        for part in re.split(r"[;,]", s):
            p = part.strip()
            if not p:
                continue
            o = option_by_name(col, p)
            if o is None and new_options is not None:
                o = add_option(col, p)
                new_options.append(o)
            if o is None:
                return None, s
            if o["id"] not in ids:
                ids.append(o["id"])
        try:
            return check(col, ids), None
        except CellError:
            return None, s
    return None, s  # link columns do not read text


def add_option(col: Col, name: str) -> dict[str, Any]:
    opts = col.options
    if len(opts) >= LIMITS["options"]:
        raise CellError(col.name, "select", detail=f"A column can have at most {LIMITS['options']} options")
    o = {"id": new_id("o"), "name": name.strip()[:100], "color": OPTION_COLORS[len(opts) % len(OPTION_COLORS)]}
    col.config = {**(col.config or {}), "options": opts + [o]}
    return o


def plain(col: Col, v: Any, *, titles: dict[str, str] | None = None) -> str:
    """A value as plain text: export, retype to text, search and the assistant. Numbers without
    grouping, dates in ISO form, options by name (joined with "; "), links by title."""
    if is_empty(v):
        return ""
    t = col.type
    if t in ("text", "number", "currency", "date"):
        return str(v)
    if t == "datetime":
        at = str(v.get("at", "")).replace("T", " ")
        return at if v.get("tz") in (None, "", "UTC") else f"{at} {v['tz']}"
    if t == "checkbox":
        return "yes" if v else ""
    if t in ("select", "multi_select"):
        names = {o["id"]: o["name"] for o in col.options}
        ids = v if isinstance(v, list) else [v]
        return "; ".join(names.get(i, "") for i in ids if names.get(i))
    if t == "link":
        tl = titles or {}
        return "; ".join(tl.get(ref_key(r), "") or ref_key(r) for r in v)
    return str(v)


# ---------------------------------------------------------------- retype


@dataclass
class Converted:
    value: Any
    raw: str | None


def convert(src: Col, dst: Col, value: Any, raw: str | None, ctx: Ctx, new_options: list[dict[str, Any]]
            ) -> Converted:
    """One cell of a column retyped from `src` to `dst`. Text kept
    in `raw` from before is read again under the new type."""
    if raw:
        v, r = parse_text(dst, raw, ctx, new_options=new_options)
        if r is None:
            return Converted(v, None)
    if is_empty(value):
        return Converted(None, raw or None)
    s, d = src.type, dst.type
    if d == "text":
        return Converted(plain(src, value) or None, None)
    if {s, d} <= {"number", "currency"}:
        return Converted(value, None)
    if s == "date" and d == "datetime":
        return Converted({"at": f"{value}T00:00", "tz": ctx.tz}, None)
    if s == "datetime" and d == "date":
        return Converted(str(value.get("at", ""))[:10] or None, None)
    if s == "select" and d == "multi_select":
        return Converted([value], None)
    if s == "multi_select" and d == "select":
        if len(value) == 1:
            return Converted(value[0], None)
        return Converted(None, plain(src, value))
    text = plain(src, value)
    v, r = parse_text(dst, text, ctx, new_options=new_options)
    return Converted(v, r)


def retype_config(src: Col, dst_type: str, config: dict[str, Any] | None) -> dict[str, Any]:
    """The new column's config: options carry over between select and multi-select; a number
    keeps its decimals and format as currency, and back."""
    cfg = dict(config or {})
    if dst_type in ("select", "multi_select") and src.type in ("select", "multi_select") and "options" not in cfg:
        cfg["options"] = src.options
    if dst_type in ("select", "multi_select"):
        cfg.setdefault("options", [])
    if dst_type in ("number", "currency") and src.type in ("number", "currency"):
        for k in ("decimals", "format"):
            if k in (src.config or {}) and k not in cfg:
                cfg[k] = src.config[k]
    return clean_config(Col(src.col_id, src.name, dst_type, cfg))


# ---------------------------------------------------------------- column config


def clean_config(col: Col, *, user_currency: str = "USD") -> dict[str, Any]:
    """A column's config with only the keys its type uses, checked."""
    cfg = col.config or {}
    t = col.type
    out: dict[str, Any] = {}
    if t == "text":
        if cfg.get("long"):
            out["long"] = True
    elif t in ("number", "currency"):
        if cfg.get("decimals") is not None:
            try:
                dec = int(cfg["decimals"])
            except (TypeError, ValueError):
                raise CellError(col.name, t, detail="decimals must be a whole number from 0 to 6") from None
            if not 0 <= dec <= 6:
                raise CellError(col.name, t, detail="decimals must be a whole number from 0 to 6")
            out["decimals"] = dec
        fmt = cfg.get("format") or "plain"
        if fmt not in ("plain", "thousands", "percent"):
            raise CellError(col.name, t, detail="format must be plain, thousands or percent")
        if t == "number" and fmt != "plain":
            out["format"] = fmt
        if t == "currency":
            cur = str(cfg.get("currency") or user_currency).upper()
            if not re.fullmatch(r"[A-Z]{3}", cur):
                raise CellError(col.name, t, detail="currency must be an ISO 4217 code such as USD")
            out["currency"] = cur
    elif t in ("select", "multi_select"):
        opts = cfg.get("options") or []
        if not isinstance(opts, list):
            raise CellError(col.name, t, detail="options must be a list")
        if len(opts) > LIMITS["options"]:
            raise CellError(col.name, t, detail=f"A column can have at most {LIMITS['options']} options")
        clean: list[dict[str, Any]] = []
        names: set[str] = set()
        ids: set[str] = set()
        for i, o in enumerate(opts):
            if isinstance(o, str):
                o = {"name": o}
            name = " ".join(str(o.get("name") or "").split())[:100]
            if not name:
                raise CellError(col.name, t, detail="an option needs a name")
            if name.lower() in names:
                raise CellError(col.name, t, detail=f"two options are called {name!r}")
            oid = str(o.get("id") or new_id("o"))[:16]
            if oid in ids:
                oid = new_id("o")
            color = o.get("color") if o.get("color") in OPTION_COLORS else OPTION_COLORS[i % len(OPTION_COLORS)]
            names.add(name.lower())
            ids.add(oid)
            clean.append({"id": oid, "name": name, "color": color})
        out["options"] = clean
    elif t == "link":
        target = cfg.get("target")
        if target not in LINK_TARGETS:
            raise CellError(col.name, t, detail=f"a link column needs a target: {', '.join(LINK_TARGETS)}")
        out["target"] = target
        if target == "table":
            tbl = str(cfg.get("table") or "")
            if not tbl.startswith("t"):
                raise CellError(col.name, t, detail="a link column to rows needs the table")
            out["table"] = tbl
        if cfg.get("multiple"):
            out["multiple"] = True
    return out


def detect(values: list[str], ctx: Ctx) -> tuple[str, float, str | None]:
    """The type that fits most of a CSV column's values, the share that converts, and a currency
    seen in most values. Text when nothing else reaches DETECT_SHARE (60 %)."""
    vals = [v.strip() for v in values if v and v.strip()]
    if not vals:
        return "text", 1.0, None
    n = len(vals)
    best: tuple[str, float] = ("text", 1.0)
    syms: dict[str, int] = {}
    num = 0
    for v in vals:
        d, sym = parse_number(v)
        if d is not None:
            num += 1
            cur = currency_of(sym)
            if cur:
                syms[cur] = syms.get(cur, 0) + 1
    cur = max(syms, key=syms.get) if syms else None
    cands = []
    if cur and syms[cur] * 2 > n:
        cands.append(("currency", num / n))
    cands.append(("number", num / n))
    cands.append(("date", sum(1 for v in vals if re.search(r"\d", v) and parse_date(v, ctx)) / n))
    words = {"yes", "no", "true", "false", "y", "n", "x", "✓", "✔", "checked", "unchecked", "done"}
    cands.append(("checkbox", sum(1 for v in vals if v.lower() in words) / n))
    for t, share in cands:
        if share >= DETECT_SHARE:
            best = (t, share)
            break
    if best[0] == "text":
        distinct = {v.lower() for v in vals}
        if 1 < len(distinct) <= min(20, max(2, n // 3)) and all(len(v) <= 40 for v in vals):
            return "select", 1.0, None
    return best[0], best[1], cur if best[0] == "currency" else None
