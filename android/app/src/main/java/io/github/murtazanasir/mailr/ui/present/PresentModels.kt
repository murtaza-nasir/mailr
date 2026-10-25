package io.github.murtazanasir.mailr.ui.present

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.data.api.MailrJson
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.JsonArray
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/*
 * The display model the server sends for what the assistant shows (schema v1,
 * spec/fixtures/agent-blocks.schema.json, built by backend/mailr/agent/present.py). Every value carries
 * `text`, the server's wording; a value type this app does not know shows that text, so a newer
 * server never breaks the card. The web renderer reads the same model, and both run the fixtures
 * in spec/fixtures/agent-blocks-fixtures.json.
 */

@Serializable
data class LabelRef(val name: String = "", val color: String? = null)

@Serializable
data class PersonRef(val name: String? = null, val email: String = "")

@Serializable
data class Condition(
    val field: String = "",
    val neg: Boolean = false,
    val values: List<String> = emptyList(),
    val labels: List<LabelRef>? = null,
    val text: String = "",
)

@Serializable
data class ChoiceItem(
    val name: String = "",
    val description: String = "",
    val then: List<DisplayField> = emptyList(),
    val text: String = "",
)

/** One typed value. [items] depends on [t]: labels, people, list (strings) or choices. */
@Serializable
data class DisplayValue(
    val t: String = "text",
    val text: String = "",
    /** The text as a msg (ServerText.kt), or several msgs shown as one list ([msgs]; for a list, one per item). */
    val msg: Msg? = null,
    val msgs: List<Msg?>? = null,
    @SerialName("after_msg") val afterMsg: Msg? = null,
    val items: JsonArray? = null,
    val more: Int = 0,
    val raw: String? = null,
    val conditions: List<Condition> = emptyList(),
    val before: String? = null,
    val after: String? = null,
    val start: String? = null,
    val end: String? = null,
    @SerialName("all_day") val allDay: Boolean = false,
    val n: Int? = null,
    val noun: String? = null,
) {
    private fun <T> decode(s: kotlinx.serialization.KSerializer<T>): List<T> =
        items?.let { catching { MailrJson.decodeFromJsonElement(ListSerializer(s), it) }.getOrNull() } ?: emptyList()

    val labels: List<LabelRef> get() = if (t == "labels") decode(LabelRef.serializer()) else emptyList()
    val people: List<PersonRef> get() = if (t == "people") decode(PersonRef.serializer()) else emptyList()
    val strings: List<String> get() = if (t == "list") decode(String.serializer()) else emptyList()
    val choices: List<ChoiceItem> get() = if (t == "choices") decode(ChoiceItem.serializer()) else emptyList()
}

@Serializable
data class DisplayField(
    val label: String = "",
    val value: DisplayValue = DisplayValue(),
    @SerialName("label_msg") val labelMsg: Msg? = null,
)

@Serializable
data class Display(
    val v: Int = 1,
    val icon: String = "action",
    val title: String = "",
    val summary: String? = null,
    val danger: Boolean = false,
    val reason: String? = null,
    val fields: List<DisplayField> = emptyList(),
    val text: String = "",
    @SerialName("title_msg") val titleMsg: Msg? = null,
    @SerialName("summary_msg") val summaryMsg: Msg? = null,
    @SerialName("reason_msg") val reasonMsg: Msg? = null,
)

@Serializable
data class Outcome(val state: String = "", val text: String = "", val msg: Msg? = null) {
    /** The outcome in the app's language (ServerText.kt). */
    fun localized(): Outcome = if (msg == null) this else copy(text = ServerText.text(msg, text))
}

private fun DisplayValue.localized(): DisplayValue = when {
    t == "list" && msgs != null -> {
        val shown = strings.mapIndexed { i, s -> ServerText.text(msgs.getOrNull(i), s) }
        copy(items = kotlinx.serialization.json.JsonArray(shown.map { kotlinx.serialization.json.JsonPrimitive(it) }))
    }
    t == "diff" && afterMsg != null -> copy(after = ServerText.text(afterMsg, after ?: text))
    t == "choices" -> copy(items = items?.let { arr ->
        kotlinx.serialization.json.JsonArray(choices.map { c ->
            MailrJson.encodeToJsonElement(ChoiceItem.serializer(), c.copy(then = c.then.map { it.localized() }))
        }.ifEmpty { arr.toList() })
    })
    msgs != null -> copy(text = ServerText.list(msgs.filterNotNull(), text))
    msg != null -> copy(text = ServerText.text(msg, text))
    else -> this
}

private fun DisplayField.localized(): DisplayField =
    copy(label = ServerText.text(labelMsg, label), value = value.localized())

/** The display in the app's language: each text with a msg beside it is rendered (ServerText.kt). */
fun Display.localized(): Display {
    val f = fields.map { it.localized() }
    val title = ServerText.text(titleMsg, title)
    val summary = summary?.let { ServerText.text(summaryMsg, it) }
    val plain = listOf(title) + listOfNotNull(summary) + f.map { (if (it.label.isNotEmpty()) "${it.label}: " else "") + it.value.text }
    return copy(title = title, summary = summary, reason = reason?.let { ServerText.text(reasonMsg, it) }, fields = f,
        text = plain.joinToString("\n"))
}

/** Wording for a status the app learned before the server's outcome arrived. */
fun localOutcome(status: String): Outcome = Outcome(status, when (status) {
    "pending" -> AppText.get(R.string.present_status_pending)
    "approved", "executing" -> AppText.get(R.string.present_status_working)
    "executed" -> AppText.get(R.string.present_status_done)
    "denied" -> AppText.get(R.string.present_status_denied)
    "expired" -> AppText.get(R.string.present_status_expired)
    "failed" -> AppText.get(R.string.present_status_failed)
    else -> status
})
