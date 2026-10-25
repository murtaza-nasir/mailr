package io.github.murtazanasir.mailr.i18n

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * The assistant's name (Settings → Ambrose → About you on the web, GET /assistant/profile
 * `options.assistant_name`), the one source for both apps (web: assistantName.ts). Every text that
 * names the assistant takes it as `%1$s`: the tab, Ask and Talk buttons, the widgets, the Quick
 * Settings tile, the launcher shortcuts, notifications and Activity.
 *
 * The last name the app saw is kept in the app's own preferences, read synchronously, so a widget, a
 * tile or a worker started before the app asked the server this run shows it at once. "Ambrose"
 * until the profile has loaded once, or when the name is not set.
 */
object AssistantName {
    /** The product's name for the assistant, and the name until the user gives it another. */
    const val DEFAULT = "Ambrose" // i18n-ignore: the product's name for the assistant

    private const val PREFS = "mailr_assistant"
    private const val KEY = "name"

    @Volatile private var state: MutableStateFlow<String>? = null
    /** Called with the new name after each change: the app refreshes its launcher shortcuts and widgets. */
    @Volatile var onChange: ((String) -> Unit)? = null

    private fun flow(): MutableStateFlow<String> = state ?: synchronized(this) {
        state ?: MutableStateFlow(load()).also { state = it }
    }

    private fun prefs(context: Context = AppText.context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private fun load(): String = catching { prefs().getString(KEY, null) }.getOrNull()?.let(::clean) ?: DEFAULT

    /** The name now changes and stays current, for Compose (`collectAsState`). */
    val name: StateFlow<String> get() = flow()

    /** The name now, outside a composable (notifications, workers, widgets, the tile). */
    fun now(): String = flow().value

    /** A name as shown: trimmed, at most 40 characters; null when blank. */
    fun clean(raw: String?): String? = raw?.trim()?.take(40)?.takeIf { it.isNotEmpty() }

    /** The name in a GET /assistant/profile answer, or null when it is not set there. */
    fun fromProfile(profile: JsonObject?): String? =
        clean(((profile?.get("options") as? JsonObject)?.get("assistant_name") as? JsonPrimitive)?.contentOrNull)

    /**
     * The name the server reported ([raw] null or blank: the default). Saved for the next start; [onChange]
     * hears of a change.
     */
    fun set(raw: String?) {
        val n = clean(raw) ?: DEFAULT
        val f = flow()
        if (f.value == n) return
        f.value = n
        catching { prefs().edit().putString(KEY, n).apply() }
        onChange?.let { catching { it(n) } }
    }

    /** Ask GET /assistant/profile through [get] and keep its name; any failure keeps the last one. */
    suspend fun refresh(get: suspend () -> JsonObject) {
        val p = try {
            get()
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (_: Exception) {
            return
        }
        set(fromProfile(p))
    }

    /** Sign-out: back to the default (the next account names its own assistant). */
    fun forget() = set(null)

    /** For tests: forget the cached state so the next read starts from the preferences. */
    fun resetForTest() {
        synchronized(this) { state = null }
        catching { prefs().edit().remove(KEY).commit() }
    }
}
