"""A scripted model for the demo instance. Development only.

Mounted by mailr/main.py at /demo-model/v1 when MAILR_DEV is on and MAILR_DEMO_ASSISTANT=1, and
registered by `mailr demo-seed` as the model endpoint "demo" (chat and embed). It speaks the
OpenAI chat-completions protocol, streaming or not, so the assistant runs exactly as with a real
model: the scripted replies call the real tools, and their cards, drafts and approvals render as
they would. Screenshots of the assistant are reproducible because nothing here is sampled.

Scripted prompts (any wording that contains the keywords):

  "What needs my attention today?"               searches unread important mail and today's calendar
  "Draft a reply to Dana confirming Thursday"    finds Dana's thread and saves a reply draft
  "Remind Marco about the quote"                 drafts a reminder and asks to send it (an approval)
  "Make a filter for Circlet notifications"      asks to create a filter (an approval)
  "Add a task to renew the studio insurance"     creates a task due on Friday
  "What did Lucía ask for?"                      reads a Spanish email and answers with quotes
  "How long should coffee rest after roasting?"  searches the web (the demo search service below)
  "Add oat milk and 2 cans of chickpeas to my shopping list under Costco"
                                                 adds two items to the Shopping list (checklist_items)

GET /demo-search/api/search answers like a degoog server with fictional results on example domains;
`mailr demo-seed` registers it as the web search provider "Web (demo)".

Anything else gets a short reply that lists these prompts. Translation (mail.translate) and reply
suggestions are answered too, and /embeddings returns hashed bag-of-words vectors, so semantic
search works offline and the same way every time.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import unicodedata
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from mailr.dev import demo_content as dc
from mailr.models import EMBED_DIM

router = APIRouter(include_in_schema=False)
MODEL = "mailr-demo"
PREFIX = "/demo-model/v1"

# ---------------------------------------------------------------- helpers


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def _local_now() -> datetime:
    """Now in Alex's zone; MAILR_DEMO_NOW (the seed's --now) pins it, so replies about "today"
    match a demo seeded for another moment."""
    pinned = os.environ.get("MAILR_DEMO_NOW", "")
    if pinned:
        d = datetime.fromisoformat(pinned)
        return (d if d.tzinfo else d.replace(tzinfo=ZoneInfo(dc.TZ))).astimezone(ZoneInfo(dc.TZ))
    return datetime.now(ZoneInfo(dc.TZ))


def next_weekday(name: str, today: date | None = None) -> date:
    """The next given weekday, at least a day ahead."""
    names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    today = today or _local_now().date()
    want = names.index(name[:3].lower())
    return today + timedelta(days=((want - today.weekday() - 1) % 7) + 1)


def fill(text: str, today: date | None = None) -> str:
    """The demo_content placeholders ({D:n}, {S:n}, {W:n}) for a given day."""
    today = today or _local_now().date()

    def day(arg: str) -> date:
        arg = arg.strip()
        return next_weekday(arg, today) if arg[:1].isalpha() else today + timedelta(days=int(arg))

    def sub(m: re.Match[str]) -> str:
        d = day(m.group(2))
        return {"D": f"{d:%A}, {d:%B} {d.day}", "S": f"{d:%b} {d.day}", "W": f"{d:%A}"}[m.group(1)]

    return re.sub(r"\{([DSW]):([^}]+)\}", sub, text)


_NAMES = {p[1]: p[0] for p in (dc.PRIYA, dc.JORDAN, dc.SAM, dc.MEI, dc.OPS, dc.DANA, dc.MARCO, dc.LUCIA, dc.CASA_AP,
                               dc.THEO, dc.NORA, dc.BEN, dc.ELENA, dc.AMARA, dc.RUTH, dc.VOLUNTEERS)}


def _who(addr: str) -> str:
    return _NAMES.get(addr, addr.split("@")[0].replace(".", " ").title() if addr and addr != "me" else "you")


def _time(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if d.tzinfo is None:
        return "all day"
    d = d.astimezone(ZoneInfo(dc.TZ))
    return f"{d:%I:%M %p}".lstrip("0")


# ---------------------------------------------------------------- scripts

Results = list[Any]  # the parsed tool results of this turn, in order
Step = Callable[[Results], dict[str, Any]]


def calls(*items: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    return {"tool_calls": [{"name": n, "arguments": a} for n, a in items]}


def say(text: str) -> dict[str, Any]:
    return {"content": text}


def _items(r: Any) -> list[dict[str, Any]]:
    return (r or {}).get("items") or [] if isinstance(r, dict) else []


def _first_conv(r: Any) -> int | None:
    items = _items(r)
    return items[0].get("conversation_id") if items else None


def _last_from(conv: Any, who: str) -> int | None:
    msgs = (conv or {}).get("messages") or [] if isinstance(conv, dict) else []
    ids = [m["message_id"] for m in msgs if who in str(m.get("from") or "") and not m.get("draft")]
    return ids[-1] if ids else None


def _txt(v: Any) -> str:
    """Tool results wrap mail text as {"untrusted": true, "content": ...}."""
    return str(v.get("content") or "") if isinstance(v, dict) else str(v or "")


_CAL_REPLY = ("Accepted:", "Declined:", "Tentative:", "New time proposed:")


def _attention_text(r: Results) -> str:
    found = _items(r[0] if r else None)
    replies = [c for c in found if _txt(c.get("subject")).startswith(_CAL_REPLY)]
    mail = [c for c in found if c not in replies][:5]
    events = ((r[1] if len(r) > 1 else None) or {}).get("events") or []
    if not mail and not events:
        return "Nothing urgent: no unread important mail and nothing on your calendar today."
    lines = []
    if mail:
        lines.append(f"{len(mail)} conversation{'s' if len(mail) != 1 else ''} need{'' if len(mail) != 1 else 's'} you:")
        lines.append("")
        for c in mail:
            sender = next((x for x in c.get("from") or [] if x != "me"), "")
            lines.append(f"- **{_txt(c.get('subject')) or '(no subject)'}**, from {_who(sender)} "
                         f"[[c:{c['conversation_id']}]]")
    if replies:
        lines += ["", "Calendar replies: " + "; ".join(
            f"{_txt(c.get('subject'))} [[c:{c['conversation_id']}]]" for c in replies[:3]) + "."]
    if events:
        lines += ["", "On your calendar today:", ""]
        for e in sorted(events, key=lambda e: (not e.get("all_day"), str(e.get("start") or "")))[:6]:
            when = "all day" if e.get("all_day") else _time(e.get("start"))
            cite = f" [[event:{e['cite']}]]" if e.get("cite") and not str(e["cite"]).startswith("[") else ""
            lines.append(f"- {when}: {e.get('summary') or 'Busy'}{cite}")
    lines += ["", "The energy bill is due in two days, and Jordan needs the Bluefin sign-off by then too."]
    return "\n".join(lines)


def _reply_dana(r: Results) -> dict[str, Any]:
    mid = _last_from(r[1], "dana@")
    if mid is None:
        return say("I could not find Dana's latest message.")
    return calls(("create_draft", {"reply_to_message_id": mid, "mode": "reply", "body": (
        "Hi Dana,\n\nConfirmed: we will share both directions on Thursday at 2 PM. I will send the deck the day "
        "before so you have time to look before the board meeting.\n\nAlex")}))


def _remind_marco(r: Results) -> dict[str, Any]:
    mine = [m["message_id"] for m in (r[1] or {}).get("messages", []) if m.get("from") == "me" and not m.get("draft")]
    if not mine:
        return say("I could not find the quote you sent Marco.")
    return calls(("create_draft", {"reply_to_message_id": mine[-1], "mode": "reply", "body": (
        "Hi Marco,\n\nA quick reminder about the quote for the landing page. If you can confirm by Monday, we "
        "can keep the team on hold for the start date.\n\nBest,\nAlex")}))


def _lucia_text(r: Results) -> str:
    mid = _last_from(r[1], "lucia@")
    if mid is None:
        return "I could not find Lucía's message."
    return (
        f"Lucía (Casa Verde) wrote in Spanish [[c:{r[1]['conversation_id']}]]. She asks for three things:\n\n"
        f"- **24 pages instead of 16**, with a new section for the outdoor collection "
        f"[[m:{mid} \"Necesitamos 24 páginas en lugar de 16\"]]\n"
        f"- She lets you know the **photos arrive** in three days, from last season's photographer "
        f"[[m:{mid} \"Las fotos llegarán\"]]\n"
        f"- An **updated quote before Friday** [[m:{mid} \"presupuesto actualizado antes del viernes\"]]\n\n"
        "Want me to draft the reply with the new quote?")


def _coffee_text(r: Results) -> str:
    urls = [i.get("url") for i in _items(r[0]) if i.get("url")]
    if len(urls) < 2:
        return "The web search found nothing useful."
    return ("Most roasters rest coffee for **4 to 7 days** before brewing espresso, and 2 to 3 days for filter "
            f"[[web:{urls[0]}]]. Lighter roasts often need the longer end of that range, because they release "
            f"gas more slowly [[web:{urls[1]}]].")


def _shopping_text(r: Results) -> str:
    res = r[0] if r and isinstance(r[0], dict) else {}
    added = [a.get("text") for x in res.get("results") or [] for a in x.get("added") or []]
    cite = (res.get("note") or {}).get("cite") or ""
    if not added:
        return "I could not add them: " + "; ".join(str(x.get("error")) for x in res.get("results") or [] if x.get("error"))
    return (f"Added **{added[0]}**" + (f" and **{added[1]}**" if len(added) > 1 else "")
            + f" to your Shopping list, under Costco {cite}.").replace(" .", ".")


def _today_range() -> dict[str, str]:
    d = _local_now().replace(hour=0, minute=0, second=0, microsecond=0)
    return {"from": d.isoformat(), "to": (d + timedelta(days=1)).isoformat()}


def _task_due() -> str:
    return next_weekday("fri").isoformat()


SCRIPTS: list[tuple[tuple[str, ...], list[Step]]] = [
    (("attention",), [
        lambda r: calls(("search_conversations", {"q": "is:unread is:important", "view": "inbox", "limit": 8}),
                        ("list_calendar", _today_range())),
        lambda r: say(_attention_text(r)),
    ]),
    (("reply", "dana"), [
        lambda r: calls(("search_conversations", {"q": "from:dana@lumencoffee.example", "limit": 3})),
        lambda r: calls(("get_conversation", {"conversation_id": _first_conv(r[0])})),
        _reply_dana,
        lambda r: say("I saved a reply to Dana confirming Thursday at 2 PM. It is in your drafts; nothing has been "
                      f"sent. [[c:{r[1]['conversation_id']}]]"),
    ]),
    (("remind", "marco"), [
        lambda r: calls(("search_conversations", {"q": "to:marco.silva@bluefin.example", "view": "sent", "limit": 3})),
        lambda r: calls(("get_conversation", {"conversation_id": _first_conv(r[0])})),
        _remind_marco,
        lambda r: calls(("send_draft", {"draft_id": (r[2] or {}).get("draft_id")})),
        lambda r: say("The reminder to Marco is ready. It waits for your approval above; once you allow it, it "
                      "goes out after the usual hold."),
    ]),
    (("filter", "circlet"), [
        lambda r: calls(("manage_filter", {"action": "create", "name": "Circlet notifications",
                                           "query": "from:notifications@circlet.example", "category": "social",
                                           "mark_read": True})),
        lambda r: say("I asked to create a filter that moves Circlet notifications to Social and marks them read. "
                      "Approve the card above to turn it on."),
    ]),
    (("task", "insurance"), [
        lambda r: calls(("create_task", {"summary": "Renew the studio insurance", "due": _task_due(), "priority": 1,
                                         "tags": ["admin"], "notes": "Policy renewal: compare the two quotes first."})),
        lambda r: say("Added the task, due Friday, with high priority"
                      + (f" [[task:{r[0]['uid']}]]." if isinstance(r[0], dict) and r[0].get("uid") else ".")),
    ]),
    (("rest", "roast"), [
        lambda r: calls(("web_search", {"query": "how long to rest coffee beans after roasting"})),
        lambda r: say(_coffee_text(r)),
    ]),
    (("shopping", "costco"), [
        lambda r: calls(("checklist_items", {"note": "shopping list", "ops": [
            {"op": "add", "lines": "oat milk, 2 cans of chickpeas", "section": "Costco"}]})),
        lambda r: say(_shopping_text(r)),
    ]),
    (("lucia",), [
        lambda r: calls(("search_conversations", {"q": "from:lucia@casaverde.example", "limit": 3})),
        lambda r: calls(("get_conversation", {"conversation_id": _first_conv(r[0])})),
        lambda r: say(_lucia_text(r)),
    ]),
]

HELP = ("This demo assistant answers a few scripted questions. Try one of these:\n\n"
        "- What needs my attention today?\n- Draft a reply to Dana confirming Thursday\n"
        "- Remind Marco about the quote\n- Make a filter for Circlet notifications\n"
        "- Add a task to renew the studio insurance\n- What did Lucía ask for?\n"
        "- How long should coffee rest after roasting?\n"
        "- Add oat milk and 2 cans of chickpeas to my shopping list under Costco")


def _turn(messages: list[dict[str, Any]]) -> tuple[str, Results]:
    """The last real user message and the tool results that followed it."""
    idx = None
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") == "user" and not str(m.get("content") or "").startswith("[mailr"):
            idx = i
            break
    if idx is None:
        return "", []
    text = messages[idx].get("content")
    if isinstance(text, list):
        text = " ".join(str(x.get("text") or "") for x in text if isinstance(x, dict))
    results: Results = []
    for m in messages[idx + 1:]:
        if m.get("role") == "tool":
            try:
                results.append(json.loads(m.get("content") or "null"))
            except ValueError:
                results.append(m.get("content"))
    return str(text or ""), results


def rewrite(sample: str, system: str) -> str:
    """A skill's "Try it": the demo sample in the formal register, others tidied (capitals, full stop)."""
    sign = (re.search(r"Sign as (.+?) if a sign-off", system) or [None, dc.ME])[1]
    if _norm("thursday works") in _norm(sample) and "dana" in sample.lower():
        return (f"Dear Dana,\n\nThank you for your message. Thursday suits me well, and I look forward to "
                f"meeting you then.\n\nKind regards,\n{sign}")
    text = " ".join(sample.split())
    return (text[:1].upper() + text[1:]).rstrip(".!") + "."


def meeting_items(meeting: str) -> list[dict[str, Any]]:
    """Action items of a demo recording: the "Name: task." lines under its summary's Action items
    heading (Alex's own become "me")."""
    m = re.search(r"#+\s*Action items\s*\n(.*?)(?:\n#|\n</summary>|$)", meeting, re.S | re.I)
    out = []
    for line in (m.group(1) if m else "").splitlines():
        hit = re.match(r"\s*[-*]\s*([^:]+):\s*(.+?)\.?\s*$", line)
        if not hit:
            continue
        who, what = hit.group(1).strip(), hit.group(2).strip()
        owner = "me" if who.split()[0] in (dc.ME.split()[0], "me", "I") else who
        out.append({"text": what[:1].upper() + what[1:], "owner": owner, "due": None, "t": None})
    return out


INTERVIEW = [
    "Lovely, a design lead! How do you like to work, and what would you most like help with in your email?",
    "That makes sense. How do your emails usually sound: warm, brief, formal?",
    "Got it. And how do you sign off?",
    "Last one: anything I should know, or avoid doing?",
    "Thank you, Alex. That is everything I need!\n[[DONE]]",
]
INTERVIEW_SUMMARY = {
    "preferred_name": "Alex", "role": "Design lead, Northwind Studio",
    "about": "I lead design at Northwind Studio, a small branding and web studio. I want help keeping up with "
             "client threads and getting quotes and invoices out on time.",
    "writing": "Warm and brief. Start with Hi and the person's name. No exclamation marks.",
    "signoff_name": "Alex", "signature": "Best,\nAlex", "languages": "English, Spanish",
    "memories": [{"text": "Keep Friday afternoons free of meetings", "kind": "preference"},
                 {"text": "Priya runs the studio reviews on Thursdays", "kind": "routine"}],
}


def respond(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages") or []
    system = str(messages[0].get("content") or "") if messages and messages[0].get("role") == "system" else ""
    last = str(messages[-1].get("content") or "") if messages else ""
    if system.startswith("You translate emails"):
        lucia = next((m for m in dc.MAILS if m["key"] == "lucia"), None)
        if lucia and _norm(lucia["subject"]) in _norm(last):
            return say("LANGUAGE: Spanish\n" + fill(lucia["translate"]))
        return say("LANGUAGE: English\n" + last.split("\n\n", 1)[-1])
    if system.startswith("You suggest short replies"):
        return say(json.dumps({"replies": ["Thanks, that works for me.", "Could we talk tomorrow?",
                                           "Let me check and get back to you."]}))
    if system.startswith("You summarise email conversations"):
        return say(json.dumps(summarize(last)))
    if system.startswith("Extract a calendar event from an email") and "bouldering wall" in last:
        sat = re.search(r"sent on \w+ (\d{4}-\d\d-\d\d)", system)
        day = date.fromisoformat(sat.group(1)) if sat else date.today()
        day += timedelta(days=(5 - day.weekday()) % 7 or 7)
        return say(json.dumps({"title": "Climbing with Theo", "start": f"{day}T10:00:00-05:00",
                               "end": f"{day}T12:30:00-05:00", "all_day": False,
                               "location": "Granite Works climbing gym",
                               "notes": "New bouldering wall, then lunch. Theo drives."}))
    if system.startswith("You rewrite text for") and "### Skill:" in system:
        return say(rewrite(last, system))
    if system.startswith("You list the action items agreed in a meeting"):
        return say(json.dumps({"items": meeting_items(last)}))
    # The setup guide's quick chat (knowledge/interview.py): one question per answer, then the summary.
    if "a friendly email assistant meeting" in system:
        answers = sum(1 for m in messages if m.get("role") == "user")
        return say(INTERVIEW[min(answers, len(INTERVIEW)) - 1])
    if "Turn what they said into a profile" in system:
        return say(json.dumps(INTERVIEW_SUMMARY))
    if body.get("response_format"):
        return say("{}")  # structured extraction: the caller falls back to its heuristics
    text, results = _turn(messages)
    if not body.get("tools"):
        if last.startswith("[mailr"):
            return say("Here is what I found so far.")
        return say(HELP)
    norm = _norm(text)
    for keys, steps in SCRIPTS:
        if all(k in norm for k in keys):
            n = min(len(results), len(steps) - 1)
            try:
                return steps[n](results)
            except (KeyError, IndexError, TypeError):
                return say("The demo data does not match this script any more; run `mailr demo-seed` again.")
    return say(HELP)


# ---------------------------------------------------------------- summaries

_CHUNK = re.compile(r"--- \[message (\d+)\] [^\n]* from ([^\n]*)\nSubject: ([^\n]*)\n(.*?)(?=\n--- \[message |\Z)", re.DOTALL)
_GREETING = re.compile(r"(?i)^(hi|hello|hey|dear|hola|thanks|thank you)\b[^.!?]*[,!]?$")


def _sentences(body: str) -> list[str]:
    lines = [ln.strip(" -*•\t") for ln in body.splitlines()]
    text = " ".join(ln for ln in lines if ln and not _GREETING.match(ln) and not ln.startswith(">")
                    and not ln.endswith(":"))
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if len(x.strip()) > 12]


def _cite(mid: str, sentence: str) -> str:
    words = sentence.split()[:12]
    quote = " ".join(words).rstrip(".,;:!?").replace('"', "'")
    return f'[[m:{mid} "{quote}"]]'


def summarize(transcript: str) -> dict[str, Any]:
    """A plain extractive summary in the shape intel/summarize.py asks for: who wrote last and
    what about, and the questions in the latest message as action items, each cited verbatim."""
    chunks = _CHUNK.findall(transcript)
    if not chunks:
        return {"summary": [], "action_items": [], "dates": []}
    mid, who, _subject, body = chunks[-1]
    who_name = "You" if who == "me" else re.sub(r"\s*<[^>]*>", "", who).strip() or who
    sents = _sentences(body)
    out = []
    if sents:
        out.append(f"{who_name} writes: {sents[0]} {_cite(mid, sents[0])}")
    if len(chunks) > 1:
        first = chunks[0]
        fs = _sentences(first[3])
        if fs:
            out.append(f"The conversation has {len(chunks)} messages; it began with: {fs[0]} {_cite(first[0], fs[0])}")
    asks = [x for x in sents if x.endswith("?") or re.match(r"(?i)(could|can|would) you|please ", x)][:3]
    return {"summary": out[:3], "action_items": [f"{x} {_cite(mid, x)}" for x in asks], "dates": []}


# ---------------------------------------------------------------- embeddings

_STOP = {"subject", "given", "question", "about", "user's", "email", "retrieve", "passages", "answer",
         "instruct", "query", "example", "there", "their", "which", "would", "could"}


def vector(text: str) -> list[float]:
    """A hashed bag of words, L2-normalised: similar wording lands close together."""
    v = [0.0] * EMBED_DIM
    for w in re.findall(r"[a-z0-9]+", _norm(text)):
        if len(w) < 4 or w in _STOP:
            continue
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        v[h % EMBED_DIM] += 1.0 if (h >> 20) % 2 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


async def embed_request(r: Any, texts: list[str], dim: int, *, timeout: float = 60) -> list[list[float]]:
    """In-process stand-in for llm.embed_request, used by the seed itself (no server running yet)."""
    return [vector(t.split("Query: ", 1)[-1]) for t in texts]


# ---------------------------------------------------------------- the endpoint


def _completion(reply: dict[str, Any]) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": reply.get("content")}
    if reply.get("tool_calls"):
        msg["tool_calls"] = [{"id": f"call_demo_{i}", "type": "function",
                              "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                             for i, c in enumerate(reply["tool_calls"])]
    return {"id": "demo", "object": "chat.completion", "model": MODEL,
            "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if reply.get("tool_calls") else "stop"}]}


async def _stream(reply: dict[str, Any]):
    def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
        return "data: " + json.dumps({"id": "demo", "object": "chat.completion.chunk", "model": MODEL,
                                      "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"

    text = reply.get("content") or ""
    for piece in re.findall(r"\S+\s*|\s+", text):
        yield chunk({"content": piece})
        await asyncio.sleep(0.008)
    for i, c in enumerate(reply.get("tool_calls") or []):
        yield chunk({"tool_calls": [{"index": i, "id": f"call_demo_{i}", "type": "function",
                                     "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}]})
    yield chunk({}, "tool_calls" if reply.get("tool_calls") else "stop")
    yield "data: [DONE]\n\n"


@router.post(PREFIX + "/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    reply = respond(body)
    if body.get("stream"):
        return StreamingResponse(_stream(reply), media_type="text/event-stream")
    return JSONResponse(_completion(reply))


@router.post(PREFIX + "/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    inp = body.get("input")
    texts = [inp] if isinstance(inp, str) else list(inp or [])
    return JSONResponse({"object": "list", "model": MODEL,
                         "data": [{"object": "embedding", "index": i, "embedding": vector(t)} for i, t in enumerate(texts)]})


@router.get(PREFIX + "/models")
async def models():
    return JSONResponse({"object": "list", "data": [{"id": MODEL, "object": "model"}]})


# ---------------------------------------------------------------- web search (degoog-style)

SEARCH_RESULTS = [
    {"title": "Resting coffee after roasting: a practical guide", "url": "https://roasters.example.org/guides/resting",
     "snippet": "Rest espresso roasts 4 to 7 days and filter roasts 2 to 3 days; taste daily to find the peak.",
     "source": "Demo index"},
    {"title": "Why light roasts need a longer rest", "url": "https://coffeescience.example.com/light-roast-degassing",
     "snippet": "Denser light roasts release carbon dioxide more slowly, so they often taste best a week after roasting.",
     "source": "Demo index"},
    {"title": "Degassing explained", "url": "https://brewjournal.example.net/degassing",
     "snippet": "Freshly roasted beans release CO2 for days; brewing too early gives uneven extraction.",
     "source": "Demo index"},
]


@router.get("/demo-search/api/search")
async def demo_search(q: str = ""):
    return JSONResponse({"results": SEARCH_RESULTS if q.strip() else [], "query": q, "totalTime": 12})
