package io.github.murtazanasir.mailr.ui.conversation

import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Translate
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.RadioButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.ui.present.AgentMarkdown
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.util.Locale
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/*
 * mail.translate in the Android reader, ported from the web's Translate.tsx: Translate… in a message's
 * menu (and a bar when the server says the body may be in another language) asks for a language, with
 * the last one chosen first. The translated text shows in place of the body, with Show original. The
 * server decides what may be translated: local-only mail goes to a local model or not at all, and its
 * plain refusal is shown as it comes.
 */

/** POST /messages/{mid}/translate (backend intel/routes.py translate). status: ready, unavailable or empty. */
@Serializable
data class TranslationResult(
    val status: String = "ready",
    val source: String? = null,
    val to: String = "English",
    val text: String = "",
    val reason: String? = null,
    val truncated: Boolean = false,
)

// ACT mail.translate @android.reader
suspend fun MailrApi.translate(messageId: Long, to: String): TranslationResult =
    client.send("POST", "messages/$messageId/translate", TranslationResult.serializer(),
        MailrJson.encodeToString(kotlinx.serialization.json.JsonObject.serializer(), buildJsonObject { put("to", to) }),
        idempotencyKey = null)

/** One message's translation in the reader. */
sealed interface Translation {
    val to: String
    data class Working(override val to: String) : Translation
    /** [showing]: the translation is in place of the body; false shows the original with "Show translation". */
    data class Ready(val result: TranslationResult, val showing: Boolean = true) : Translation {
        override val to get() = result.to
    }
    /** The server would not translate it (no model, local-only mail, nothing to translate): its words. */
    data class Refused(override val to: String, val reason: String) : Translation
    data class Failed(override val to: String, val message: String) : Translation
}

object TranslateLanguages {
    /** Names the server takes (letters, spaces, hyphens and brackets), in English. */
    val all = listOf("English", "Spanish", "French", "German", "Portuguese", "Italian", "Dutch", "Polish", "Swedish",
        "Danish", "Norwegian", "Finnish", "Greek", "Turkish", "Russian", "Ukrainian", "Arabic", "Hebrew", "Persian",
        "Urdu", "Hindi", "Bengali", "Chinese (Simplified)", "Chinese (Traditional)", "Japanese", "Korean",
        "Vietnamese", "Thai", "Indonesian", "Malay", "Filipino", "Swahili")

    private val labels = mapOf(
        "English" to R.string.conv_lang_english,
        "Spanish" to R.string.conv_lang_spanish,
        "French" to R.string.conv_lang_french,
        "German" to R.string.conv_lang_german,
        "Portuguese" to R.string.conv_lang_portuguese,
        "Italian" to R.string.conv_lang_italian,
        "Dutch" to R.string.conv_lang_dutch,
        "Polish" to R.string.conv_lang_polish,
        "Swedish" to R.string.conv_lang_swedish,
        "Danish" to R.string.conv_lang_danish,
        "Norwegian" to R.string.conv_lang_norwegian,
        "Finnish" to R.string.conv_lang_finnish,
        "Greek" to R.string.conv_lang_greek,
        "Turkish" to R.string.conv_lang_turkish,
        "Russian" to R.string.conv_lang_russian,
        "Ukrainian" to R.string.conv_lang_ukrainian,
        "Arabic" to R.string.conv_lang_arabic,
        "Hebrew" to R.string.conv_lang_hebrew,
        "Persian" to R.string.conv_lang_persian,
        "Urdu" to R.string.conv_lang_urdu,
        "Hindi" to R.string.conv_lang_hindi,
        "Bengali" to R.string.conv_lang_bengali,
        "Chinese (Simplified)" to R.string.conv_lang_chinese_simplified,
        "Chinese (Traditional)" to R.string.conv_lang_chinese_traditional,
        "Japanese" to R.string.conv_lang_japanese,
        "Korean" to R.string.conv_lang_korean,
        "Vietnamese" to R.string.conv_lang_vietnamese,
        "Thai" to R.string.conv_lang_thai,
        "Indonesian" to R.string.conv_lang_indonesian,
        "Malay" to R.string.conv_lang_malay,
        "Filipino" to R.string.conv_lang_filipino,
        "Swahili" to R.string.conv_lang_swahili,
    )

    /** [name] (a name the server takes) in the app's language; a name the list lacks stays as it is. */
    fun label(name: String): String = labels[name]?.let { AppText.get(it) } ?: name

    /** The phone's own language, when the list has it, else English. */
    fun phoneDefault(locale: Locale = Locale.getDefault()): String {
        val name = locale.getDisplayLanguage(Locale.ENGLISH)
        if (name == "Chinese") return if (locale.script == "Hant" || locale.country in setOf("TW", "HK", "MO")) "Chinese (Traditional)" else "Chinese (Simplified)"
        return all.firstOrNull { it.equals(name, ignoreCase = true) } ?: "English"
    }

    /** The list with [last] first, so the usual choice is one tap. */
    fun ordered(last: String): List<String> = listOf(last) + all.filter { it != last }

    /** "Translated from Spanish to English (the first part)." */
    fun doneLine(r: TranslationResult): String =
        when {
            r.source != null && r.truncated -> AppText.get(R.string.conv_translated_from_to_part, label(r.source), label(r.to))
            r.source != null -> AppText.get(R.string.conv_translated_from_to, label(r.source), label(r.to))
            r.truncated -> AppText.get(R.string.conv_translated_to_part, label(r.to))
            else -> AppText.get(R.string.conv_translated_to, label(r.to))
        }
}

/** The last language picked on this phone (a convenience; losing it only resets the choice). */
class TranslatePrefs(context: Context) {
    private val sp = context.getSharedPreferences("translate", Context.MODE_PRIVATE)

    var last: String
        get() = sp.getString("last", null)?.takeIf { it in TranslateLanguages.all } ?: TranslateLanguages.phoneDefault()
        set(v) { sp.edit().putString("last", v).apply() }
}

/** Choose the language; [last] is selected and first. */
@Composable
fun TranslatePicker(last: String, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    var choice by remember(last) { mutableStateOf(last) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.conv_translate_to)) },
        text = {
            LazyColumn(Modifier.heightIn(max = 360.dp).testTag("translate-languages")) {
                items(TranslateLanguages.ordered(last), key = { it }) { lang ->
                    Row(Modifier.fillMaxWidth().selectable(choice == lang, role = Role.RadioButton) { choice = lang }
                        .padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(choice == lang, onClick = null)
                        Spacer(Modifier.width(12.dp))
                        Text(TranslateLanguages.label(lang), style = MaterialTheme.typography.bodyLarge)
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { onPick(choice) }, modifier = Modifier.testTag("translate-go")) { Text(stringResource(R.string.conv_translate_go)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.conv_cancel)) } },
    )
}

/**
 * The bar above a message's body: the offer (when the server says so), Translating…, the refusal,
 * or "Translated from X to Y" with Show original / Show translation.
 */
@Composable
fun TranslateBar(t: Translation?, offer: Boolean, onOffer: () -> Unit, onShow: (Boolean) -> Unit, onRetry: () -> Unit) {
    if (t == null && !offer) return
    Surface(color = MaterialTheme.colorScheme.surfaceContainerHigh, shape = RoundedCornerShape(10.dp),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp).testTag("translate-bar")
            .semantics { liveRegion = LiveRegionMode.Polite }) {
        Row(Modifier.padding(start = 12.dp, end = 4.dp).heightIn(min = 44.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (t is Translation.Working) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
            else Icon(Icons.Outlined.Translate, null, Modifier.size(18.dp),
                tint = if (t is Translation.Failed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
            val (line, action) = when (t) {
                null -> stringResource(R.string.conv_translate_offer) to (stringResource(R.string.conv_translate) to onOffer)
                is Translation.Working -> stringResource(R.string.conv_translating_to, TranslateLanguages.label(t.to)) to null
                is Translation.Refused -> t.reason to null
                is Translation.Failed -> stringResource(R.string.conv_translate_failed) to (stringResource(R.string.conv_try_again) to onRetry)
                is Translation.Ready -> if (t.showing) TranslateLanguages.doneLine(t.result) to (stringResource(R.string.conv_show_original) to { onShow(false) })
                    else stringResource(R.string.conv_translated_to, TranslateLanguages.label(t.result.to)) to (stringResource(R.string.conv_show_translation) to { onShow(true) })
            }
            Text(line, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f).padding(vertical = 8.dp),
                color = if (t is Translation.Failed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface)
            action?.let { (label, f) -> TextButton(onClick = f) { Text(label) } }
        }
    }
}

/** The translated text, in place of the body. */
@Composable
fun TranslatedBody(r: TranslationResult) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp).testTag("translated-body")) {
        AgentMarkdown(r.text)
    }
}
