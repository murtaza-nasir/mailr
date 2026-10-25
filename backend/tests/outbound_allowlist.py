"""Every literal `sensitive=False` / `query_sensitive=False` in mailr/, with the reason it is safe
(tests/test_sensitive_allowlist.py). A key is "<path under mailr/>::<enclosing function>::<callee>::
<keyword>"; the value is (how many such calls that function makes, the reason).

A new literal False needs an entry here, and the entry needs a reason a reviewer can check. Most
are one of three kinds: an availability probe or endpoint lookup that sends nothing (the call
that sends content declares its own class later), a resolve-then-filter pass (the endpoint is
looked up once and local-only rows are left out when it is hosted), or a new row whose class is
"not sensitive" until something marks it.
"""

PROBE = "an availability probe or endpoint lookup: no user content is sent"
RESOLVE_THEN_FILTER = ("the endpoint is resolved once; when it is hosted, local-only and sensitive rows are "
                       "left out of the batch below")
NEW_ROW = "a new row's initial mark; content that is local-only marks it later"

ALLOWED: dict[str, tuple[int, str]] = {
    "agent/transcribe.py::available::llm.available::sensitive": (
        1, PROBE + " (GET /me features.transcribe; each clip declares its context's class)"),
    "agent/loop.py::get_session::ChatSession::sensitive": (
        1, NEW_ROW + " (a chat session turns sensitive when it reads local-only content)"),
    "agent/loop.py::build_system::llm.resolve::sensitive": (
        1, PROBE + " (probe=True: is the chat model local, for the memory index)"),
    "agent/tools.py::local_ok::llm.resolve::sensitive": (
        1, PROBE + " (probe=True: is the chat model local)"),
    "api/mailgroup_routes.py::cleanup_suggest::llm.resolve::sensitive": (
        1, PROBE + " (probe=True; the job's model calls pass each message's local_only)"),
    "api/model_prefs.py::my_models::llm.resolve_embed::sensitive": (
        1, PROBE + " (Settings shows which embedding endpoint is in use)"),
    "api/onboarding_routes.py::_assistant::llm.available::sensitive": (1, PROBE),
    "api/onboarding_routes.py::chat::llm.available::sensitive": (1, PROBE),
    "api/onboarding_routes.py::chat::llm.chat::sensitive": (
        1, "the setup interview: the user's own answers typed into it, no mail or stored content"),
    "api/onboarding_routes.py::chat_summary::llm.chat::sensitive": (
        1, "the setup interview's transcript: the user's own answers, no mail or stored content"),
    "cli.py::_reembed::embed.current::sensitive": (1, PROBE + " (is an embedding endpoint configured)"),
    "core/mailgroup_tasks.py::suggest::llm.resolve::sensitive": (
        1, PROBE + " (probe=True; the guesses below pass each message's local_only)"),
    "dev/e2e.py::_memory_extras.ent::Entity::sensitive": (1, NEW_ROW + " (end-to-end seed data)"),
    "dev/e2e.py::_memory_extras.note::Memory::sensitive": (1, NEW_ROW + " (end-to-end seed data)"),
    "dev/e2e.py::_memory_extras::ChatSession::sensitive": (1, NEW_ROW + " (end-to-end seed data)"),
    "dev/e2e.py::_chat_extras::ChatSession::sensitive": (1, NEW_ROW + " (end-to-end seed data)"),
    "intel/embed.py::embed_pending_passages::current::sensitive": (
        1, RESOLVE_THEN_FILTER + " (Message.local_only and local-only connectors)"),
    "intel/embed.py::backfill_message_models::current::sensitive": (
        1, PROBE + " (the model key to stamp; no text is embedded)"),
    "intel/routes.py::status::llm.available::sensitive": (1, PROBE + " (the status page's 'any' column)"),
    "intel/routes.py::_embedding_status::embed.current::sensitive": (1, PROBE + " (the model key for counts)"),
    "intel/routes.py::reembed::embed.current::sensitive": (1, PROBE + " (is an embedding endpoint configured)"),
    "intel/tasks.py::intel_embed_pending::embed.current::sensitive": (
        1, PROBE + " (the model key; embed_pending_passages filters local-only mail)"),
    "knowledge/memory.py::self_entity::Entity::sensitive": (
        1, NEW_ROW + " (the user's own entity, made with no notes)"),
    "knowledge/memory.py::embed_pending::emb.current::sensitive": (
        1, RESOLVE_THEN_FILTER + " (Memory.sensitive and sensitive entities)"),
    "knowledge/tasks.py::extract_for_user::llm.available::sensitive": (
        1, PROBE + " (is any chat model there; the extraction calls pass each group's class)"),
}
