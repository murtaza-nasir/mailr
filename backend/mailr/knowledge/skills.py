"""Skills: named prompts the user invokes as /name (in Help me write or the assistant chat).

A skill has a slug `name`, a `description` saying when to use it (the assistant reads the
descriptions to pick a skill itself), and `instructions`, the prompt body. `applies_to` limits
where a skill is offered and where an `auto` skill (a writing default) is applied without being
invoked; an explicit /name always works.

Built-in skills are seeded per user the first time skills are used. They are ordinary rows: the
user may edit, disable or delete them, and "reset" restores the shipped text. A deleted built-in
is not seeded again (the profile remembers which built-ins were seeded).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import Skill

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
# /name as a word of its own: at the start or after a space or "(", and not followed by more path or
# a file extension, so "and/or", "9/29", "/etc/hosts" and "https://…" are left alone. The older
# //name spelling still works.
INVOKE_RE = re.compile(r"(?<![^\s(])(/{1,2})([A-Za-z0-9][A-Za-z0-9_-]{0,47})(?![\w/:-]|\.\w)")
CONTEXTS = ("compose", "reply", "chat", "summarize")
MAX_INSTRUCTIONS = 8000

BUILTINS: dict[str, dict[str, Any]] = {
    "email-basics": {
        "title": "Writing defaults",
        "description": "Applied to every draft: the user's voice, name and sign-off, no placeholders.",
        "applies_to": ["compose", "reply"], "auto": True,
        "instructions": (
            "- Write as the user, in the first person, in the language of the conversation.\n"
            "- End with a short closing and the user's sign-off name. Do not add a signature block; mailr adds it.\n"
            "- Never write placeholders such as [Your name], [Name] or [Company]. Use what you know; "
            "leave out what you do not know, or mark one missing fact as [TBD: what is missing].\n"
            "- Short paragraphs, plain text, no subject line inside the body.\n"
            "- Do not invent facts, dates, amounts or promises."),
    },
    "formal": {
        "title": "Formal",
        "description": "A formal, professional register: official mail, first contact, senior people.",
        "applies_to": ["compose", "reply", "chat"],
        "instructions": (
            "- Greeting: \"Dear <title and surname>,\" (or \"Dear <first name>,\" if they wrote that way).\n"
            "- State the purpose in the first sentence.\n"
            "- Complete sentences, no contractions, no slang, no emoji or exclamation marks.\n"
            "- Courteous and precise; short paragraphs.\n"
            "- Closing: \"Kind regards,\" then the sign-off name."),
    },
    "friendly": {
        "title": "Friendly",
        "description": "Warm and relaxed: colleagues, friends, people the user knows well.",
        "applies_to": ["compose", "reply", "chat"],
        "instructions": (
            "- Greeting: \"Hi <first name>,\".\n"
            "- Warm, natural tone; contractions are fine.\n"
            "- One friendly line is enough; do not gush.\n"
            "- Closing: \"Thanks,\" or \"Best,\" then the first name."),
    },
    "concise": {
        "title": "Concise",
        "description": "The shortest version that keeps every fact, question and request.",
        "applies_to": ["compose", "reply", "chat", "summarize"],
        "instructions": (
            "- Keep every fact, question, date and request; cut everything else.\n"
            "- At most five sentences. Use a short list when there are several items.\n"
            "- One greeting line and one closing line at most; no filler (\"I hope this finds you well\")."),
    },
    "polite-decline": {
        "title": "Polite decline",
        "description": "Say no to a request or invitation clearly and kindly.",
        "applies_to": ["compose", "reply"],
        "instructions": (
            "- Thank them, then decline clearly within the first two sentences.\n"
            "- Give a reason only if the user gave one; do not invent one.\n"
            "- Offer an alternative (another time, another person) only if the user mentioned it.\n"
            "- No over-apologising. End on a warm note that keeps the door open."),
    },
    "follow-up": {
        "title": "Follow-up",
        "description": "A gentle nudge when someone has not answered an earlier message.",
        "applies_to": ["compose", "reply"],
        "instructions": (
            "- Refer to the earlier message briefly (its topic or date).\n"
            "- Restate the open question or request in one sentence.\n"
            "- Suggest a concrete next step or a date if the user gave one.\n"
            "- Friendly and short; no guilt-tripping."),
    },
    "configure-mailr": {
        "title": "Configure mailr",
        "description": "Use when the user wants to change how mailr is set up: filters, labels, settings, "
                       "inbox layout, signature, profile.",
        "applies_to": ["chat"],
        "instructions": (
            "1. Read the current state first: get_configuration (section filters, labels, settings, layouts or "
            "identities).\n"
            "2. Pick the tool:\n"
            "   - Filters (\"put mail from X under label Y\", \"archive newsletters\"): manage_filter. The query uses "
            "search syntax (from:, to:, subject:, list:, has:attachment, -word); add_labels takes label names; set "
            "create_missing_labels to make new labels; apply_existing to run it on old mail too.\n"
            "   - Labels (create, rename, nest with \"Parent/Child\", colour \"#rrggbb\", hide): manage_label.\n"
            "   - Settings (theme, density, reading pane, page size, undo send, notifications, inbox tabs): "
            "update_settings with only the keys to change.\n"
            "   - Inbox layout (priority inbox, custom tabs and sections): manage_layout.\n"
            "   - Signature: update_signature. Name, sign-off, time zone, writing preferences: update_profile.\n"
            "   - Skills: create_skill / update_skill. Notes about people and projects: remember.\n"
            "3. Make one change per request unless the user asked for several. Most changes wait for the user's "
            "approval; say what is waiting.\n"
            "4. Confirm in one sentence what changed. The user can undo it from the chat.\n"
            "5. Sign-in, security, sending addresses, accounts and API tokens are changed only by the user in "
            "Settings; say so if asked."),
    },
    "create-skill": {
        "title": "Create a skill",
        "description": "Use when the user wants a new skill (a reusable writing style or instruction set).",
        "applies_to": ["chat"],
        "instructions": (
            "You help the user write a new skill for mailr.\n"
            "1. If the purpose is unclear, ask one short question. Otherwise do not ask.\n"
            "2. Pick a short lowercase name with hyphens (it is typed as /name). Check list_skills to avoid "
            "a clash.\n"
            "3. Write: title; description (one sentence: when to use it); instructions (3-8 short, concrete "
            "bullet points in the imperative); applies_to (compose, reply, chat, summarize).\n"
            "4. Call create_skill. The user reviews and approves the new skill in mailr.\n"
            "5. Tell the user the name and how to use it, e.g. \"/name draft a reply to…\"."),
    },
}


def validate_name(name: str) -> str:
    n = (name or "").strip().lower().lstrip("/")
    if not NAME_RE.match(n):
        raise ValueError("a skill name uses lowercase letters, digits, - and _ (at most 48), e.g. polite-decline")
    from mailr.agent.commands import RESERVED

    if n in RESERVED:
        raise ValueError(f"/{n} is a chat command; choose another name for the skill")
    return n


def clean(data: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "name" in data or not partial:
        out["name"] = validate_name(str(data.get("name") or ""))
    for k, n in (("title", 200), ("description", 1000)):
        if k in data and data[k] is not None:
            out[k] = " ".join(str(data[k]).split())[:n]
    if "instructions" in data and data["instructions"] is not None:
        text = str(data["instructions"]).strip()
        if len(text) > MAX_INSTRUCTIONS:
            raise ValueError(f"instructions are limited to {MAX_INSTRUCTIONS} characters")
        out["instructions"] = text
    if not partial and not out.get("instructions"):
        raise ValueError("a skill needs instructions")
    if "applies_to" in data and data["applies_to"] is not None:
        v = data["applies_to"]
        v = [v] if isinstance(v, str) else list(v)
        bad = [x for x in v if x not in CONTEXTS]
        if bad:
            raise ValueError(f"applies_to must be among {', '.join(CONTEXTS)}")
        out["applies_to"] = sorted(set(v), key=CONTEXTS.index)
    for k in ("auto", "enabled"):
        if k in data and data[k] is not None:
            out[k] = bool(data[k])
    if not partial and not out.get("title"):
        out["title"] = out["name"].replace("-", " ").replace("_", " ").capitalize()
    return out


def out(s: Skill) -> dict[str, Any]:
    return {"id": s.id, "name": s.name, "title": s.title, "description": s.description,
            "instructions": s.instructions, "applies_to": list(s.applies_to or []), "auto": s.auto,
            "enabled": s.enabled, "builtin": s.builtin, "source": s.source,
            "modified": bool(s.builtin and s.name in BUILTINS and _differs(s, BUILTINS[s.name])),
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None}


def _differs(s: Skill, b: dict[str, Any]) -> bool:
    return (s.title != b["title"] or s.description != b["description"] or s.instructions != b["instructions"]
            or list(s.applies_to or []) != b["applies_to"] or s.auto != b.get("auto", False))


async def seed(db: AsyncSession, user_id: int) -> None:
    """Add the built-in skills this user has not had yet (never re-adds a deleted one)."""
    from mailr.knowledge import profile

    prof = await profile.get(db, user_id)
    seeded = set((prof.state or {}).get("skills_seeded") or [])
    missing = [n for n in BUILTINS if n not in seeded]
    if not missing:
        return
    have = set((await db.execute(select(Skill.name).where(Skill.user_id == user_id))).scalars())
    for n in missing:
        if n not in have:
            db.add(Skill(user_id=user_id, name=n, builtin=True, source="builtin", enabled=True,
                         **{k: v for k, v in BUILTINS[n].items()}))
    prof.state = {**(prof.state or {}), "skills_seeded": sorted(seeded | set(missing))}
    await db.flush()


def reset_fields(name: str) -> dict[str, Any]:
    b = BUILTINS[name]
    return {"title": b["title"], "description": b["description"], "instructions": b["instructions"],
            "applies_to": list(b["applies_to"]), "auto": b.get("auto", False), "enabled": True}


async def restore_builtins(db: AsyncSession, user_id: int) -> list[str]:
    """Re-add deleted built-ins (the "Restore built-in skills" button)."""
    have = set((await db.execute(select(Skill.name).where(Skill.user_id == user_id))).scalars())
    added = []
    for n in BUILTINS:
        if n not in have:
            db.add(Skill(user_id=user_id, name=n, builtin=True, source="builtin", **reset_fields(n)))
            added.append(n)
    await db.flush()
    return added


async def all_for(db: AsyncSession, user_id: int, *, enabled_only: bool = False) -> list[Skill]:
    await seed(db, user_id)
    q = select(Skill).where(Skill.user_id == user_id)
    if enabled_only:
        q = q.where(Skill.enabled.is_(True))
    return list((await db.execute(q.order_by(Skill.builtin.desc(), Skill.name))).scalars())


async def by_name(db: AsyncSession, user_id: int, name: str) -> Skill | None:
    await seed(db, user_id)
    return (await db.execute(select(Skill).where(Skill.user_id == user_id,
                                                 Skill.name == name.strip().lower().lstrip("/")))).scalar_one_or_none()


# ---------------------------------------------------------------- invocation


@dataclass
class Invocation:
    text: str  # the prompt with /name tokens removed
    skills: list[Skill] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # friendly messages for unknown / disabled names

    @property
    def names(self) -> list[str]:
        return [s.name for s in self.skills]


def _tokens(text: str) -> list[tuple[str, bool]]:
    """(name, explicit) per distinct token. Explicit = typed as //name or as the prompt's first word;
    only those report an unknown name (a lone "/word" mid-sentence may just be text)."""
    seen: dict[str, bool] = {}
    lead = len(text or "") - len((text or "").lstrip())
    for m in INVOKE_RE.finditer(text or ""):
        n = m.group(2).lower().rstrip("-_")
        if n:
            seen[n] = seen.get(n, False) or m.group(1) == "//" or m.start() == lead
    return list(seen.items())


async def _skills_place(db: AsyncSession, user_id: int) -> str:
    from mailr.knowledge import profile

    return f"Settings → {await profile.name_of(db, user_id)} → Skills"


async def resolve(db: AsyncSession, user_id: int, text: str) -> Invocation:
    """Find /name tokens, look them up, and strip them from the prompt."""
    toks = _tokens(text)
    if not toks:
        return Invocation(text=text)
    rows = {s.name: s for s in await all_for(db, user_id)}
    inv = Invocation(text=text)
    where = None  # "Settings → Ambrose → Skills", with the user's name for the assistant
    from mailr.agent.commands import RESERVED

    for n, explicit in toks:
        s = rows.get(n)
        if s is None and n in RESERVED:
            continue  # a slash command (agent/commands.py), not a skill
        if s is None:
            if explicit:
                close = difflib.get_close_matches(n, list(rows), n=1, cutoff=0.6)
                where = where or await _skills_place(db, user_id)
                inv.problems.append(f"There is no skill /{n}." + (f" Did you mean /{close[0]}?" if close else
                                                                   f" See {where}."))
        elif not s.enabled:
            where = where or await _skills_place(db, user_id)
            inv.problems.append(f"The skill /{n} is turned off ({where}).")
        else:
            inv.skills.append(s)
    stripped = INVOKE_RE.sub(lambda m: "" if m.group(2).lower().rstrip("-_") in rows else m.group(0), text)
    inv.text = re.sub(r"[ \t]{2,}", " ", stripped).strip()
    return inv


def instructions_block(skills: list[Skill], heading: str = "Skills to apply") -> str:
    if not skills:
        return ""
    parts = [f"{heading} (written by the user; follow them):"]
    for s in skills:
        parts.append(f"### /{s.name} — {s.title}\n{s.instructions.strip()}")
    return "\n\n".join(parts)


async def auto_for(db: AsyncSession, user_id: int, context: str, exclude: list[str] | None = None) -> list[Skill]:
    rows = await all_for(db, user_id, enabled_only=True)
    ex = set(exclude or [])
    return [s for s in rows if s.auto and s.name not in ex and (not s.applies_to or context in s.applies_to)]


def catalogue(skills: list[Skill], context: str = "chat", limit: int = 30) -> str:
    """One line per skill for the assistant to choose from."""
    rows = [s for s in skills if s.enabled and not s.auto and (not s.applies_to or context in s.applies_to
                                                               or "compose" in s.applies_to or "reply" in s.applies_to)]
    return "\n".join(f"- /{s.name}: {s.description or s.title}" for s in rows[:limit])
