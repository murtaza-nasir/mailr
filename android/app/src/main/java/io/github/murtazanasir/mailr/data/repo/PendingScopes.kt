package io.github.murtazanasir.mailr.data.repo

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.db.PendingOpEntity
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/**
 * What a queued change depends on, and how Pending changes names it.
 *
 * Two mail actions depend on each other when they share a conversation; the calendar's, task
 * and reminder changes keep their own order among themselves (a create comes before its edit).
 * A change held back by a failure holds back only the changes that share a scope with it.
 */
object PendingScopes {
    fun of(op: PendingOpEntity): Set<String> {
        val path = op.path.substringBefore('?').trim('/')
        if (path == "conversations/actions") {
            val ids = catching {
                ((MailrJson.parseToJsonElement(op.body) as JsonObject)["ids"] as JsonArray).map { "c:" + (it as JsonPrimitive).content }
            }.getOrNull()
            return if (ids.isNullOrEmpty()) setOf("mail") else ids.toSet()
        }
        return setOf("other")
    }

    /** "Archive 2 conversations": the text a queued mail action carries. */
    fun describe(action: String, n: Int): String {
        val id = when (action) {
            "archive" -> R.plurals.sync_queued_archive
            "trash" -> R.plurals.sync_queued_trash
            "untrash" -> R.plurals.sync_queued_untrash
            "spam" -> R.plurals.sync_queued_spam
            "not_spam" -> R.plurals.sync_queued_not_spam
            "delete_forever" -> R.plurals.sync_queued_delete_forever
            "read" -> R.plurals.sync_queued_read
            "unread" -> R.plurals.sync_queued_unread
            "star" -> R.plurals.sync_queued_star
            "unstar" -> R.plurals.sync_queued_unstar
            "important" -> R.plurals.sync_queued_important
            "not_important" -> R.plurals.sync_queued_not_important
            "snooze" -> R.plurals.sync_queued_snooze
            "mute" -> R.plurals.sync_queued_mute
            "unmute" -> R.plurals.sync_queued_unmute
            "add_labels" -> R.plurals.sync_queued_add_labels
            "remove_labels" -> R.plurals.sync_queued_remove_labels
            "move" -> R.plurals.sync_queued_move
            "follow_up" -> R.plurals.sync_queued_follow_up
            "cancel_follow_up" -> R.plurals.sync_queued_cancel_follow_up
            else -> return AppText.plural(R.plurals.sync_queued_other, n, action.replace('_', ' ').replaceFirstChar { it.titlecase() }, n)
        }
        return AppText.plural(id, n, n)
    }

    /**
     * A queued snooze whose time has passed (by more than a minute) at [nowMs]. The replay drops
     * it rather than sending it: the phone's own reminder fired at that time already, and a late
     * snooze could undo a dismissal made since on another device.
     */
    fun elapsedSnooze(op: PendingOpEntity, nowMs: Long): Boolean {
        if (op.method != "POST" || op.path.trim('/') != "reminders/snooze") return false
        val until = catching {
            ((kotlinx.serialization.json.Json.parseToJsonElement(op.body) as kotlinx.serialization.json.JsonObject)["until"]
                as kotlinx.serialization.json.JsonPrimitive).content
        }.getOrNull() ?: return false
        val at = catching { java.time.OffsetDateTime.parse(until).toInstant().toEpochMilli() }
            .recoverCatching { java.time.Instant.parse(until).toEpochMilli() }.getOrNull() ?: return false
        return at < nowMs - 60_000
    }

    /**
     * Why a change failed, as a sentence, for Pending changes. The server's reason is quoted, so
     * it does not read like the name of an item ("snooze until a time in the future" did).
     */
    fun failure(error: String?): String {
        val reason = error?.trim()?.trimEnd('.')?.takeIf { it.isNotEmpty() } ?: return AppText.get(R.string.sync_not_synced)
        return AppText.get(R.string.sync_not_synced_reason, reason)
    }

    /** The text Pending changes shows; changes queued by builds before 0.5.3 said "archive 2". */
    fun title(op: PendingOpEntity): String {
        val legacy = Regex("^([a-z_]+) (\\d+)$").matchEntire(op.description)
        if (legacy != null && op.path.trim('/') == "conversations/actions")
            return describe(legacy.groupValues[1], legacy.groupValues[2].toInt())
        return op.description
    }
}
