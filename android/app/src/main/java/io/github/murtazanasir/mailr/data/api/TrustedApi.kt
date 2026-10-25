package io.github.murtazanasir.mailr.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// Trusted addresses (backend core/trusted.py, assistant/trusted_routes.py): addresses of the
// user's own that mailr is not connected to. An address counts once the code mailr mails to it is
// confirmed. The identity page only: what Ambrose does with a forwarded note is the built-in standing
// order Follow my forwarded notes (OrdersApi.kt).
// Unknown fields are ignored.

@Serializable
data class TrustedLastMessage(
    val subject: String = "",
    val at: String? = null,
    /** Its own domain's authentication verified it (DKIM or DMARC): a note in it counts. */
    val verified: Boolean = false,
)

@Serializable
data class TrustedAddress(
    val id: Long = 0,
    val email: String = "",
    /** confirmed, pending (a code is out) or expired (send a new code). */
    val state: String = "pending",
    @SerialName("confirmed_at") val confirmedAt: String? = null,
    @SerialName("code_expires_at") val codeExpiresAt: String? = null,
    @SerialName("last_message") val lastMessage: TrustedLastMessage? = null,
) {
    val confirmed: Boolean get() = state == "confirmed"
}

@Serializable
data class TrustedSuggestion(val email: String = "", val name: String = "", val messages: Int = 0)

@Serializable
data class TrustedListing(
    val addresses: List<TrustedAddress> = emptyList(),
    val suggestions: List<TrustedSuggestion> = emptyList(),
    /** The addresses another assistant handles (the user setting handled_elsewhere). */
    @SerialName("handled_elsewhere") val handledElsewhere: List<String> = emptyList(),
    @SerialName("code_minutes") val codeMinutes: Int = 30,
)

private const val TRUSTED = "assistant/trusted-addresses"

// ACT trusted.list @android.settings
suspend fun MailrApi.trustedAddresses(): TrustedListing = client.get(TRUSTED, TrustedListing.serializer())

// ACT trusted.add @android.settings
suspend fun MailrApi.addTrusted(email: String): TrustedAddress =
    client.send("POST", TRUSTED, TrustedAddress.serializer(),
        MailrJson.encodeToString(JsonObject.serializer(), buildJsonObject { put("email", email) }))

// ACT trusted.confirm @android.settings
suspend fun MailrApi.confirmTrusted(id: Long, code: String): TrustedAddress =
    client.send("POST", "$TRUSTED/$id/confirm", TrustedAddress.serializer(),
        MailrJson.encodeToString(JsonObject.serializer(), buildJsonObject { put("code", code) }))

// ACT trusted.resend @android.settings
suspend fun MailrApi.resendTrusted(id: Long): TrustedAddress =
    client.send("POST", "$TRUSTED/$id/resend", TrustedAddress.serializer())

// ACT trusted.remove @android.settings
suspend fun MailrApi.removeTrusted(id: Long): JsonObject = client.send("DELETE", "$TRUSTED/$id", JsonObject.serializer())
