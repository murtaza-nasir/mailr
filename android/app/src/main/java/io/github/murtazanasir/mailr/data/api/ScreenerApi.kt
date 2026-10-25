package io.github.murtazanasir.mailr.data.api

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// The sender screener (backend core/screener.py): GET /screener lists the first-time senders waiting,
// POST /screener/{id}/decide allows or blocks one. Unknown fields are ignored.

@Serializable
data class ScreenerLatest(
    @SerialName("conversation_id") val conversationId: Long = 0,
    val subject: String = "",
    val snippet: String = "",
    val date: String? = null,
)

@Serializable
data class ScreenerAuth(val level: String = "unknown", val text: String = "")

@Serializable
data class ScreenerSender(
    val id: Long = 0,
    val key: String = "",
    /** "address" or "domain": what the decision covers. */
    val kind: String = "address",
    val email: String = "",
    val name: String = "",
    val messages: Int = 0,
    val unread: Int = 0,
    val latest: ScreenerLatest = ScreenerLatest(),
    val auth: ScreenerAuth = ScreenerAuth(),
    val account: String = "",
) {
    val title: String get() = name.ifBlank { email }

    /** As the web says it: "Everyone at example.com" or "This address only". */
    val scope: String get() = if (kind == "domain") AppText.get(R.string.api_screener_scope_domain, key) else AppText.get(R.string.api_screener_scope_address)

    val authLabel: String get() = when (auth.level) {
        "verified" -> AppText.get(R.string.api_screener_auth_verified)
        "failed" -> AppText.get(R.string.api_screener_auth_failed)
        else -> AppText.get(R.string.api_screener_auth_none)
    }

    val held: String get() = AppText.plural(R.plurals.api_screener_held, messages, messages)
}

@Serializable
data class ScreenerList(val enabled: Boolean = false, val count: Int = 0, val senders: List<ScreenerSender> = emptyList())

// ACT screener.review @android.screener
suspend fun MailrApi.screener(): ScreenerList = client.get("screener", ScreenerList.serializer())

/** Allow (decision "allow") or block (decision "block", to Spam) a sender; their waiting mail moves at once. */
suspend fun MailrApi.screenerDecide(id: Long, decision: String): JsonObject =
    client.send("POST", "screener/$id/decide", JsonObject.serializer(),
        MailrJson.encodeToString(JsonObject.serializer(), buildJsonObject { put("decision", decision) }))
