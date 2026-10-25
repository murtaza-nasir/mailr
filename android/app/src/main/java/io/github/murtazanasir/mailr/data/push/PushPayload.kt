package io.github.murtazanasir.mailr.data.push

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.data.api.MailrJson
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * One message from mailr through the UnifiedPush distributor: the JSON document of
 * backend/mailr/core/push.py (`up_payload`), decrypted by the connector when the server
 * encrypted it for this phone's keys.
 *
 * | type | fields |
 * |---|---|
 * | `new_mail`, `important_mail` | title (the sender, or "mailr" for a local-only account), body "New message", conversation_id, message_id, click |
 * | `approval` | title "Approval request from <agent>", body (the request in one line), pending_id + action, or outbox_id (the older queue), click |
 * | `send_failed` | title "Message not sent", body "To …: <error>", outbox_id, click |
 * | `assistant` | title, body, and the run's ids |
 * | `test` | title "mailr", body "Test notification" |
 * | `approval.decided` | no title or body; ids (the approvals that left pending, decided anywhere or expired), outcome |
 *
 * Ids are numbers (public ids when the server has them on); a string of digits is read too.
 */
sealed interface PushEvent {
    val title: String
    val body: String

    data class NewMail(val conversationId: Long?, val messageId: Long?, val important: Boolean,
                       override val title: String, override val body: String) : PushEvent

    data class Approval(val pendingId: Long?, val outboxId: Long?, val action: String?,
                        override val title: String, override val body: String,
                        /** Who asks (`actor`; the title is in the account's language, so it is not parsed). */
                        val actor: String? = null) : PushEvent {
        /** The key the poller and the Approvals screen use (NotifyDecisions.approvalKey). */
        val key: String? get() = outboxId?.let { "o$it" } ?: pendingId?.toString()
    }

    /**
     * Approvals that are no longer pending: decided on another device, covered by a grant, expired
     * or withdrawn. It carries no text; [keys] are the notification keys (NotifyDecisions.approvalKey).
     */
    data class ApprovalDecided(val ids: List<Long>, val outcome: String) : PushEvent {
        override val title: String get() = ""
        override val body: String get() = ""
        val keys: List<String> get() = ids.map { it.toString() }
    }

    data class SendFailed(val outboxId: Long?, override val title: String, override val body: String) : PushEvent

    data class Assistant(val runId: Long?, val conversationId: Long?, override val title: String, override val body: String) : PushEvent

    data class Test(override val title: String, override val body: String) : PushEvent

    /** A mail account needs its password again (sent once per failure). */
    data class Account(val connectorId: Long?, override val title: String, override val body: String) : PushEvent

    /** A type this version does not know: it still means "something changed", so the app syncs. */
    data class Other(val type: String, override val title: String, override val body: String) : PushEvent
}

object PushPayload {
    fun decode(bytes: ByteArray): PushEvent? {
        val obj = catching { MailrJson.parseToJsonElement(bytes.decodeToString()) as? JsonObject }.getOrNull() ?: return null
        fun str(k: String) = (obj[k] as? JsonPrimitive)?.contentOrNull
        fun id(k: String) = str(k)?.toLongOrNull()
        val title = str("title").orEmpty()
        val body = str("body").orEmpty()
        return when (val type = str("type") ?: return null) {
            "new_mail", "important_mail" -> PushEvent.NewMail(id("conversation_id"), id("message_id"), type == "important_mail", title, body)
            "approval" -> PushEvent.Approval(id("pending_id"), id("outbox_id"), str("action"), title, body, str("actor"))
            "approval.decided" -> PushEvent.ApprovalDecided(
                (obj["ids"] as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.contentOrNull?.toLongOrNull() }.orEmpty(),
                str("outcome").orEmpty())
            "send_failed" -> PushEvent.SendFailed(id("outbox_id"), title, body)
            "assistant" -> PushEvent.Assistant(id("run_id"), id("conversation_id"), title, body)
            "test" -> PushEvent.Test(title, body)
            "account" -> PushEvent.Account(id("connector_id"), title, body)
            else -> PushEvent.Other(type, title, body)
        }
    }
}
