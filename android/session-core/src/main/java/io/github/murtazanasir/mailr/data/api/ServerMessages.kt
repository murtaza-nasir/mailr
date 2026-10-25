package io.github.murtazanasir.mailr.data.api

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import android.content.SharedPreferences
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.Request

/**
 * The server's error texts in the app's language. An error answer may carry a
 * message code and its values (`{"detail": …, "code": "conversation.not_found", "params": {…}}`,
 * [ApiException.messageCode]); `GET /api/v1/i18n/messages?lang=<tag>` (no sign-in) answers the
 * catalogue `{"lang", "version", "messages": {code: "text with {{name}}"}}`.
 *
 * The catalogue is kept in memory and in the app's preferences, so it is there at the next start
 * before any network. [refresh] fetches it again when the language changes, the server changes or a
 * day has passed. A server without the endpoint (404) or no network leaves the catalogue as it was;
 * [Throwable.userMessage] then shows the app's own texts.
 */
object ServerMessages {
    /** How long a catalogue stays fresh. */
    const val MAX_AGE_MS: Long = 24L * 60 * 60 * 1000

    private const val PREFS = "mailr_server_messages"
    private const val K_LANG = "lang"
    private const val K_SERVER = "server"
    private const val K_VERSION = "version"
    private const val K_FETCHED = "fetched_at"
    private const val K_MESSAGES = "messages"

    private val PLACEHOLDER = Regex("""\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}""")

    @Volatile private var prefs: SharedPreferences? = null
    @Volatile private var loaded: State? = null

    /** The catalogue, read from the preferences on first use (not at the app's start). */
    private var state: State
        get() = loaded ?: synchronized(this) { loaded ?: load().also { loaded = it } }
        set(value) { loaded = value }

    private fun load(): State {
        val p = prefs ?: return State()
        return catching {
            val msgs = p.getString(K_MESSAGES, null)?.let { decodeMessages(it) } ?: emptyMap()
            State(p.getString(K_LANG, null), p.getString(K_SERVER, null), p.getString(K_VERSION, null), p.getLong(K_FETCHED, 0), msgs)
        }.getOrDefault(State())
    }

    /** The catalogue as last fetched. */
    data class State(
        val lang: String? = null,
        val server: String? = null,
        val version: String? = null,
        val fetchedAt: Long = 0,
        val messages: Map<String, String> = emptyMap(),
    )

    /** The current catalogue (for tests and diagnostics). */
    val current: State get() = state

    /**
     * Where the stored catalogue is. Both Application classes call it at start; the catalogue itself
     * is decoded on first use (an error text), so a start or a background wake does not pay for it.
     */
    fun init(context: Context) {
        prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        loaded = null
    }

    /**
     * Fetches the catalogue for [lang] (a BCP 47 tag) from [client]'s server when the stored one is for
     * another language or server, or older than a day. Never fails: a 404 (an older server), a bad
     * answer or a network error leaves the catalogue as it was. Cancellation still cancels.
     */
    suspend fun refresh(client: ApiClient, lang: String, now: Long = System.currentTimeMillis()) {  // clock-ok: refresh interval
        try {
            val server = client.baseUrl().toString()
            val s = state
            if (s.lang == lang && s.server == server && s.fetchedAt > 0 && now - s.fetchedAt in 0 until MAX_AGE_MS) return
            val request = Request.Builder().url(client.url("i18n/messages", mapOf("lang" to lang))).get().build()
            val body = client.raw(request).use { r ->
                if (!r.isSuccessful) return
                withContext(Dispatchers.IO) { r.body?.string() }
            } ?: return
            val obj = MailrJson.parseToJsonElement(body) as? JsonObject ?: return
            val messages = (obj["messages"] as? JsonObject)?.let { m ->
                m.mapNotNull { (k, v) -> (v as? JsonPrimitive)?.takeIf { it.isString }?.let { k to it.content } }.toMap()
            } ?: return
            val version = (obj["version"] as? JsonPrimitive)?.content
            val next = State(lang, server, version, now, messages)
            state = next
            prefs?.edit()
                ?.putString(K_LANG, lang)
                ?.putString(K_SERVER, server)
                ?.putString(K_VERSION, version)
                ?.putLong(K_FETCHED, now)
                ?.putString(K_MESSAGES, encodeMessages(messages))
                ?.apply()
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            // Kept as it was: the app's own texts stand in.
        }
    }

    /** The catalogue's text for [code] with its `{{name}}` placeholders filled from [params], or null. */
    fun text(code: String?, params: Map<String, String>): String? {
        if (code.isNullOrBlank()) return null
        val template = state.messages[code]?.takeIf { it.isNotBlank() } ?: return null
        return interpolate(template, params)
    }

    /** Fills `{{name}}` from [params]; a placeholder without a value stays as it is. */
    fun interpolate(template: String, params: Map<String, String>): String =
        PLACEHOLDER.replace(template) { m -> params[m.groupValues[1]] ?: m.value }

    /** Forgets the catalogue (tests). */
    fun clear() {
        state = State()
        prefs?.edit()?.clear()?.apply()
    }

    private fun encodeMessages(m: Map<String, String>): String =
        buildJsonObject { m.forEach { (k, v) -> put(k, v) } }.toString()

    private fun decodeMessages(text: String): Map<String, String> =
        (MailrJson.parseToJsonElement(text) as? JsonObject)
            ?.mapNotNull { (k, v) -> (v as? JsonPrimitive)?.let { k to it.content } }?.toMap() ?: emptyMap()
}
