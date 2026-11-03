package io.github.murtazanasir.mailr.ui.conversation

import io.github.murtazanasir.mailr.data.api.Message

/**
 * A saved draft keeps its message id through every save (the web app, the assistant, an approval,
 * another device), so a body loaded for it once goes stale. What the conversation says about a
 * draft changes with every save: its Date header, subject and snippet. The reader compares these
 * after each refresh of the conversation and loads again the bodies of the drafts that changed.
 */
internal fun draftRevision(m: Message): String? =
    if (m.draft) "${m.date.orEmpty()}\u0000${m.subject}\u0000${m.snippet}" else null

/** The drafts in [messages] whose revision differs from [seen] (a draft not in [seen] is new, not changed). */
internal fun changedDrafts(seen: Map<Long, String>, messages: List<Message>): List<Long> =
    messages.mapNotNull { m ->
        val rev = draftRevision(m) ?: return@mapNotNull null
        val before = seen[m.id] ?: return@mapNotNull null
        m.id.takeIf { before != rev }
    }

/** The revisions of the drafts in [messages], to compare the next refresh with. */
internal fun draftRevisions(messages: List<Message>): Map<Long, String> =
    messages.mapNotNull { m -> draftRevision(m)?.let { m.id to it } }.toMap()
