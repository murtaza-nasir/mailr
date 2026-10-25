package io.github.murtazanasir.mailr.ui.present

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.i18n.AppText
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull

/*
 * Server texts kept as a key: an approval card's title,
 * summary, reason, field labels and fixed values, its outcome, and the assistant's tool steps come
 * with a msg ({key, params}) beside their English. The app renders the msg from its own copy of the
 * server's `approvals` section (strings_server.xml and ServerKeys.kt, written by
 * tools/i18n_server_texts.py), so the words follow the app's language. A key the app does not
 * have, or no msg, shows the server's English.
 */

/** A catalogue key and its parameters; a parameter may itself be a msg. */
@Serializable
data class Msg(val key: String = "", val params: JsonObject = JsonObject(emptyMap()))

object ServerText {
    private val placeholder = Regex("""\{\{\s*(\w+)\s*\}\}""")

    /** Keys whose `{{name}}` is the assistant's name: an older server sends them without it. */
    private val ASSISTANT_NAMED = setOf("approvals.forward.summary")

    /** [m] in the app's language, else [fallback]. */
    fun text(m: Msg?, fallback: String): String = m?.let { render(it.key, it.params) } ?: fallback

    /** A msg sent as JSON (a stream event), else [fallback]. */
    fun text(m: JsonElement?, fallback: String): String {
        val o = m as? JsonObject ?: return fallback
        val key = (o["key"] as? JsonPrimitive)?.contentOrNull ?: return fallback
        return render(key, o["params"] as? JsonObject ?: JsonObject(emptyMap())) ?: fallback
    }

    /** Several msgs as one list ("a card on the email, a notification"), else [fallback]. */
    fun list(ms: List<Msg>?, fallback: String): String {
        if (ms.isNullOrEmpty()) return fallback
        val parts = ms.map { render(it.key, it.params) ?: return fallback }
        return parts.joinToString(", ")
    }

    private fun render(key: String, params: JsonObject): String? {
        val values = HashMap<String, String>()
        var count: Int? = null
        for ((k, v) in params) {
            when {
                v is JsonNull -> Unit
                v is JsonObject -> values[k] = text(v, "").ifEmpty { return null }
                v is JsonPrimitive -> {
                    values[k] = v.content
                    if (k == "count") count = v.intOrNull
                }
            }
        }
        val raw = catching {
            ServerKeys.strings[key]?.let { AppText.get(it) }
                ?: ServerKeys.plurals[key]?.let { AppText.context.resources.getQuantityString(it, count ?: 0) }
        }.getOrNull() ?: return null
        if (key in ASSISTANT_NAMED && "name" !in values) values["name"] = io.github.murtazanasir.mailr.i18n.AssistantName.now()
        return placeholder.replace(raw) { m -> values[m.groupValues[1]] ?: m.value }
    }
}
