package io.github.murtazanasir.mailr.data.voice

import io.github.murtazanasir.mailr.data.agent.AskAbout

/**
 * What a dictated clip is spoken into, for the server's data rule (POST /assistant/transcribe
 * `context`, agent/transcribe.py): `chat:<id>` for a chat that exists, `note:<id>` and `task:<uid>`
 * for a note, task or event the words are about, `none` for a new chat about nothing. Anything
 * else (a new chat about an email, a recording, a table) sends no context, so the server treats the
 * clip as local-only and uses a local speech server only.
 */
object DictationContext {
    fun of(sessionId: String?, about: AskAbout?, conversation: Long? = null): String? = when {
        sessionId != null -> "chat:$sessionId"
        conversation != null -> null
        about == null -> "none"
        about is AskAbout.Note && about.kind != "table" -> "note:${about.id}"
        about is AskAbout.Task -> "task:${about.uid}"
        about is AskAbout.Event -> "task:${about.uid}"
        else -> null
    }
}
