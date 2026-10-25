package io.github.murtazanasir.mailr.data.api

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * One row of GET /activity: a change that you, Ambrose, one of your agents or your
 * administrator made recently. The phone shows the list read-only; Undo is on the web.
 */
@Serializable
data class ActivityItem(
    val id: String = "",
    val at: String = "",
    /** you, ambrose, agent, admin or system. */
    val who: String = "",
    /** The agent's or the administrator's name; null for you and Ambrose. */
    val by: String? = null,
    /** mail, calendar, tasks, notes, tables, settings or account. */
    val app: String = "",
    val text: String = "",
    val undone: Boolean = false,
    val ok: Boolean = true,
    /** The forward Ambrose was acting on (Trusted addresses): its conversation and subject. */
    val via: ActivityVia? = null,
) {
    /** "You", "Ambrose" (the user's name for the assistant), "Research agent", "Your administrator (Sam Lee)". */
    val actor: String get() = when (who) {
        "you" -> AppText.get(R.string.api_activity_you)
        "ambrose" -> io.github.murtazanasir.mailr.i18n.AssistantName.now()
        "agent" -> by?.takeIf { it.isNotBlank() } ?: AppText.get(R.string.api_activity_an_agent)
        "admin" -> by?.takeIf { it.isNotBlank() }?.let { AppText.get(R.string.api_activity_admin_named, it) } ?: AppText.get(R.string.api_activity_admin)
        else -> "Mailr"
    }
}

@Serializable
data class ActivityVia(val kind: String = "", val title: String = "")

@Serializable
data class ActivityPage(val items: List<ActivityItem> = emptyList(), @SerialName("next_cursor") val nextCursor: String? = null)

/** GET /activity: the newest first, a page at a time. */
suspend fun MailrApi.activity(cursor: String? = null, limit: Int = 50): ActivityPage =
    client.get("activity", ActivityPage.serializer(), mapOf("cursor" to cursor, "limit" to limit))
