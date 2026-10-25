package io.github.murtazanasir.mailr.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// Settings → Ambrose → Standing orders (backend assistant/orders.py, routes.py; decision
// 2026-10-09-orders-consolidate). The phone lists the orders, turns each on or off, and sets the
// built-in order's permission (Ask me or Allow). Creating and editing them stays on the web.
// Unknown fields are ignored.

@Serializable
data class StandingOrder(
    val id: Long = 0,
    val name: String = "",
    /** message, schedule or recording. */
    val trigger: String = "message",
    val query: String = "",
    val enabled: Boolean = true,
    val position: Int = 0,
    /** "forwarded_notes" for the built-in order Follow my forwarded notes; null for the user's own. */
    val builtin: String? = null,
    /** The built-in order's permission: ask or allow. */
    val permission: String? = null,
    @SerialName("from_trusted") val fromTrusted: Boolean = false,
    @SerialName("from_me") val fromMe: Boolean = false,
)

@Serializable
data class StandingOrders(val items: List<StandingOrder> = emptyList())

// The orders, top first (GET /assistant/orders).
suspend fun MailrApi.standingOrders(): StandingOrders = client.get("assistant/orders", StandingOrders.serializer())

// ACT order.pause @android.settings (and resume)
suspend fun MailrApi.setOrderEnabled(id: Long, enabled: Boolean): StandingOrder =
    client.send("PATCH", "assistant/orders/$id", StandingOrder.serializer(),
        MailrJson.encodeToString(JsonObject.serializer(), buildJsonObject { put("enabled", enabled) }))

// ACT order.permission @android.settings
suspend fun MailrApi.setOrderPermission(id: Long, permission: String): StandingOrder =
    client.send("PATCH", "assistant/orders/$id", StandingOrder.serializer(),
        MailrJson.encodeToString(JsonObject.serializer(), buildJsonObject { put("permission", permission) }))

/** The addresses another assistant handles (the user setting handled_elsewhere). */
suspend fun MailrApi.setHandledElsewhere(addresses: List<String>): JsonObject =
    patchSettings(JsonObject(mapOf("handled_elsewhere" to JsonArray(addresses.map { JsonPrimitive(it) }))))
