"""Slash commands in the assistant chat: one list for the web, Android and the server.

A command is typed at the start of a message (`/today`, `/task call the bank fri 3p`). It differs
from a skill (knowledge/skills.py), which adds the user's own instructions to a question: a
command does something. Commands come first: a skill cannot take a command's name, and the
command wins where an older skill already has one.

Three kinds:
- **app**: done by the app itself (start a chat, rename it, copy a reply…). A client that does not
  know one gets a short reply saying so (`reply_for`).
- **info**: answered by the server at once, without a model (`/model`).
- **ask**: turned into a clear request to the assistant (`request_for`). What the user typed stays
  in the chat; the request is what the model reads. The tools, permissions and local-only routing
  are the chat's own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mailr import i18n


@dataclass(frozen=True)
class Command:
    name: str
    kind: str  # "app" | "info" | "ask"
    group: str  # "chat" | "add" | "ask": where the picker and /help list it
    arg: str = "none"  # "none" | "optional" | "required"
    aliases: tuple[str, ...] = ()


COMMANDS: tuple[Command, ...] = (
    # The chat itself (the app does these).
    Command("new", "app", "chat", aliases=("clear",)),
    Command("chats", "app", "chat", "optional"),
    Command("rename", "app", "chat", "required"),
    Command("retitle", "app", "chat"),
    Command("delete", "app", "chat"),
    Command("retry", "app", "chat"),
    Command("copy", "app", "chat"),
    Command("note", "app", "chat"),
    Command("local", "app", "chat"),
    Command("model", "info", "chat"),
    Command("help", "app", "chat"),
    # Adding things.
    Command("task", "ask", "add", "required"),
    Command("event", "ask", "add", "required"),
    Command("remember", "ask", "add", "required"),
    Command("forget", "ask", "add", "required"),
    # Ready-made questions.
    Command("today", "ask", "ask"),
    Command("week", "ask", "ask"),
    Command("summarize", "ask", "ask", "optional"),
    Command("reply", "ask", "ask", "optional"),
    Command("waiting", "ask", "ask"),
    Command("find", "ask", "ask", "required"),
    # A bulk review of the view on screen (agent/review.py); /cleanup was its first name.
    Command("sweep", "ask", "ask", "optional", aliases=("cleanup",)),
)

_BY_NAME = {n: c for c in COMMANDS for n in (c.name, *c.aliases)}
RESERVED = frozenset(_BY_NAME)

# A command opens the message: `/name`, then the end or a space and its text. `//name` is a skill.
_LEADING = re.compile(r"^/([A-Za-z][A-Za-z0-9_-]*)(?:\s+(.*))?$", re.S)


def parse(text: str | None) -> tuple[Command, str] | None:
    """The command the message starts with and the text after it, or None."""
    m = _LEADING.match((text or "").strip())
    if not m:
        return None
    cmd = _BY_NAME.get(m.group(1).lower())
    return (cmd, (m.group(2) or "").strip()) if cmd else None


# What the model reads for an `ask` command. English instructions to the model (it answers in the
# user's language); `{text}` is what the user typed after the command.
_REQUESTS: dict[str, str] = {
    "task": "Create a task: {text}\nTake the due date, time, list and priority from those words. Make no other "
            "changes, and tell me briefly what you created.",
    "event": "Create a calendar event: {text}\nTake the date, time, length, place and guests from those words. "
             "Do not invite anyone I did not name. Tell me briefly what you created.",
    "remember": "Remember this: {text}",
    "forget": "Forget what you remember about: {text}\nShow me what you remove.",
    "today": "Give me an overview of today, in three short parts: my calendar for today, the tasks due "
             "today or overdue, and the unread mail that needs me (needs a reply, a deadline, or important). "
             "When no task is due today, say that nothing is due today and name the next task due; call my "
             "task list empty only when it has no open tasks at all.",
    "week": "Give me an overview of this week, from today to Sunday: my events day by day, the tasks and "
            "deadlines that fall due, and the mail still waiting for my reply.",
    "summarize": "Summarize {target} in a few lines, then list any questions for me and anything I need to do, "
                 "with dates.",
    "reply": "Draft a reply to {target}.{guidance} Save it as a draft for me to review; do not send it.",
    "waiting": "List the mail I sent that is still waiting for an answer (my follow-ups), oldest first: who, "
               "about what, and since when.",
    "find": "Search my mail for: {text}\nList what matches (sender, subject, date), newest first, with links. "
            "Read a conversation in full only if the list cannot answer.",
    "sweep": "Review every conversation in {target} with review_mail and propose what to trash, archive, label "
             "or mark read on one review card.{guidance} Nothing changes before I approve.",
}


def request_for(cmd: Command, text: str) -> str:
    """The request the model reads for an `ask` command."""
    t = text.strip()
    if cmd.name in ("summarize", "reply"):
        target = t if t and cmd.name == "summarize" else "the conversation open on my screen"
        guidance = f" What to say: {t}" if t and cmd.name == "reply" else ""
        return _REQUESTS[cmd.name].format(target=target, guidance=guidance)
    if cmd.name == "sweep":
        return _REQUESTS["sweep"].format(target="the list on my screen (scope \"screen\")",
                                         guidance=f" My wishes, for its instructions: {t}" if t else "")
    return _REQUESTS[cmd.name].format(text=t)


def missing_text(cmd: Command, text: str) -> bool:
    return cmd.arg == "required" and not text.strip()


def summary(cmd: Command, lang: str | None = None, name: str = "Ambrose") -> str:
    return i18n.t(f"assistant.commands.{cmd.name}", lang, name=name)


def listing(lang: str | None = None, name: str = "Ambrose") -> list[dict]:
    """GET /agent/commands: the commands in picker order, with what each does in the user's language."""
    return [{"name": c.name, "aliases": list(c.aliases), "kind": c.kind, "group": c.group, "arg": c.arg,
             "summary": summary(c, lang, name),
             "example": i18n.t(f"assistant.commands.example.{c.name}", lang) if c.arg == "required" else None} for c in COMMANDS]


def reply_for(cmd: Command, text: str, lang: str | None = None) -> str | None:
    """A reply the server gives itself, without a model: for an app command from an app that does
    not have it, and for a command that needs text and got none. None: the command runs."""
    if cmd.kind == "app":
        return i18n.t("assistant.commands.app_only", lang, command=f"/{cmd.name}")
    if missing_text(cmd, text):
        return i18n.t("assistant.commands.needs_text", lang, command=f"/{cmd.name}",
                      example=i18n.t(f"assistant.commands.example.{cmd.name}", lang))
    return None
