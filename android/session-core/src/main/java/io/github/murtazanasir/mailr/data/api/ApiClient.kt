package io.github.murtazanasir.mailr.data.api

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.session.R
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * The server answered with an error status. [body] is the raw answer (the 409 conflict body carries the server copy).
 * [messageCode] and [params] are the error's message code and its values (`"code"` and `"params"` in the body),
 * which [ServerMessages] turns into text in the app's language; older servers and unconverted errors send neither.
 */
class ApiException(
    val code: Int,
    val detail: String,
    val body: String? = null,
    val messageCode: String? = null,
    val params: Map<String, String> = emptyMap(),
) : Exception(detail) {
    val retryable: Boolean get() = code == 408 || code == 429 || code >= 500
}

val MailrJson = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    coerceInputValues = true
    encodeDefaults = true
    isLenient = true
}

/**
 * HTTP transport for the mailr API.
 *
 * The device session travels as the `mailr_session` cookie, and mutations carry
 * `X-CSRF-Token` plus an `Idempotency-Key`, so a request retried after a dropped connection
 * (or replayed from the offline outbox) is applied once.
 */
class ApiClient(
    private val sessions: SessionStore,
    baseClient: OkHttpClient? = null,
    /**
     * This install's id, sent as `X-Mailr-Client` on every request: the live events of its own
     * writes come back with it as `origin`, so an open editor skips them (core/live.py).
     */
    val clientId: String = newClientId(),
    /**
     * The app and its version ("mailr-android/0.6.0", "mailr-android-notes/1.0.0"). The server reads it
     * for its oldest-supported-version check (core/appversion.py) and to tell device writes apart.
     */
    val userAgent: String = "mailr-android",
    /**
     * The device's zone (an IANA name) and clock ("12h" or "24h"), sent as `X-Mailr-Zone` and
     * `X-Mailr-Clock` to the configured server: the user's zone and clock while Region and Time
     * format are Automatic, so Ambrose writes times as the phone shows them (backend calendar/tz.py).
     * Read for each request, since either may change in the system settings. Null sends nothing.
     */
    private val deviceZone: () -> String? = { java.util.TimeZone.getDefault().id },
    private val deviceClock: () -> String? = { null },
) {

    val http: OkHttpClient = (baseClient ?: OkHttpClient.Builder().build()).newBuilder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(120, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .addInterceptor { chain ->
            val req = chain.request()
            // Plain HTTP goes only to the server the user entered (a LAN server, network_security_config).
            if (!req.url.isHttps && !sameOrigin(req.url)) throw IOException("Refusing plain HTTP to ${req.url.host}")
            val resp = chain.proceed(req.newBuilder()
                .header("User-Agent", userAgent)
                .header("X-Mailr-Device", "android")
                .header("X-Mailr-Client", clientId)
                .apply {
                    if (sameOrigin(req.url)) {
                        deviceZone()?.let { header("X-Mailr-Zone", it) }
                        deviceClock()?.let { header("X-Mailr-Clock", it) }
                    }
                }
                .build())
            noteVersion(req.url, resp.code)
            resp
        }
        // The session goes on each request as it leaves, and only to the configured server: a
        // network interceptor runs again for every redirect hop, so a redirect to another host
        // (or another scheme or port) leaves without the cookie and the CSRF token.
        .addNetworkInterceptor { chain ->
            val req = chain.request()
            val s = sessions.session.value
            if (s == null || !sameOrigin(req.url)) return@addNetworkInterceptor chain.proceed(req)
            val b = req.newBuilder().header("Cookie", "mailr_session=${s.token}")
            if (req.method != "GET") b.header("X-CSRF-Token", s.csrf)
            chain.proceed(b.build())
        }
        .build()

    /**
     * Told when the server refuses this app version (HTTP 426, core/appversion.py) and when a later
     * API call goes through again. [io.github.murtazanasir.mailr.data.update.AppUpdates] is the listener.
     */
    interface VersionListener {
        fun upgradeRequired()
        fun accepted()
    }

    @Volatile var versionListener: VersionListener? = null

    /**
     * A 426 from the configured server means this version is below its floor. A success means it is
     * served again; the download and release paths (/api/v1/app/) and the health check do not count,
     * since a refused version may still reach them.
     */
    private fun noteVersion(u: HttpUrl, code: Int) {
        val l = versionListener ?: return
        if (!sameOrigin(u)) return
        if (code == 426) l.upgradeRequired()
        else if (code in 200..299 && "/api/v1/app/" !in u.encodedPath && "/api/v1/health" !in u.encodedPath) l.accepted()
    }

    /** Whether [u] is on the configured server: same scheme, host and port. */
    fun sameOrigin(u: HttpUrl): Boolean {
        val base = catching { baseUrl() }.getOrNull() ?: return false
        return u.scheme == base.scheme && u.host.equals(base.host, ignoreCase = true) && u.port == base.port
    }

    /** Emits when the server says the session is gone (401). The UI returns to sign-in. */
    val signedOut: SharedFlow<Unit> get() = _signedOut
    private val _signedOut = MutableSharedFlow<Unit>(extraBufferCapacity = 1, onBufferOverflow = BufferOverflow.DROP_OLDEST)

    /** The API root. No usable server address (none set yet) fails like an unreachable server. */
    fun baseUrl(): HttpUrl = (sessions.server + "/api/v1/").toHttpUrlOrNull()
        ?: throw IOException("No server address")

    fun url(path: String, query: Map<String, Any?> = emptyMap()): HttpUrl {
        val b = baseUrl().newBuilder()
        // A stored offline operation may carry its query in the path ("x/y?scope=all").
        val (p, q) = path.split('?', limit = 2).let { it[0] to it.getOrNull(1) }
        p.trimStart('/').split('/').filter { it.isNotEmpty() }.forEach { b.addPathSegment(it) }
        q?.split('&')?.filter { it.isNotEmpty() }?.forEach { kv ->
            val (k, v) = kv.split('=', limit = 2).let { it[0] to it.getOrElse(1) { "" } }
            b.addQueryParameter(java.net.URLDecoder.decode(k, "UTF-8"), java.net.URLDecoder.decode(v, "UTF-8"))
        }
        query.forEach { (k, v) -> if (v != null) b.addQueryParameter(k, v.toString()) }
        return b.build()
    }

    suspend fun <T> get(path: String, ser: KSerializer<T>, query: Map<String, Any?> = emptyMap()): T =
        execute(Request.Builder().url(url(path, query)).get().build(), ser)

    suspend fun <T> send(
        method: String,
        path: String,
        ser: KSerializer<T>,
        body: String? = null,
        idempotencyKey: String? = UUID.randomUUID().toString(),
        query: Map<String, Any?> = emptyMap(),
        headers: Map<String, String> = emptyMap(),
    ): T {
        val rb: RequestBody = (body ?: "{}").toRequestBody(JSON_TYPE)
        val req = Request.Builder().url(url(path, query)).method(method, rb)
        if (idempotencyKey != null) req.header("Idempotency-Key", idempotencyKey)
        headers.forEach { (k, v) -> req.header(k, v) }
        return execute(req.build(), ser)
    }

    suspend fun raw(request: Request): Response = request.await()

    suspend fun <T> execute(request: Request, ser: KSerializer<T>): T {
        val resp = request.await()
        resp.use { r ->
            val text = withContext(Dispatchers.IO) { r.body?.string().orEmpty() }
            if (!r.isSuccessful) {
                if (r.code == 401 && sessions.session.value != null) _signedOut.tryEmit(Unit)
                throw ApiException(r.code, detailOf(text) ?: r.message.ifBlank { "HTTP ${r.code}" }, text,
                    messageCode = messageCodeOf(text), params = paramsOf(text))
            }
            return MailrJson.decodeFromString(ser, text.ifBlank { "{}" })
        }
    }

    private suspend fun Request.await(): Response = suspendCancellableCoroutine { cont ->
        val call = http.newCall(this)
        cont.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (!cont.isCancelled) cont.resumeWithException(e)
            }

            override fun onResponse(call: Call, response: Response) {
                cont.resume(response)
            }
        })
    }

    companion object {
        /** The phone's clock for `X-Mailr-Clock`: "24h" or "12h", as the system setting says. */
        fun clockOf(context: android.content.Context): String =
            if (android.text.format.DateFormat.is24HourFormat(context)) "24h" else "12h"

        /** A random client id ("android-" and 16 hex digits; the server accepts letters, digits and -_:.). */
        fun newClientId(): String = "android-" + UUID.randomUUID().toString().replace("-", "").take(16)

        /** This install's client id, kept in the app's preferences. */
        fun installId(context: android.content.Context): String {
            val prefs = context.getSharedPreferences("mailr_client", android.content.Context.MODE_PRIVATE)
            prefs.getString("client_id", null)?.let { return it }
            return newClientId().also { prefs.edit().putString("client_id", it).apply() }
        }

        val JSON_TYPE = "application/json; charset=utf-8".toMediaType()

        fun detailOf(text: String): String? = catching {
            val obj = MailrJson.parseToJsonElement(text) as? JsonObject ?: return null
            val d = obj["detail"] ?: return null
            catching { d.jsonPrimitive.content }.getOrNull() ?: d.toString()
        }.getOrNull()

        /** The error's message code ("conversation.not_found"), or null when the body has none. */
        fun messageCodeOf(text: String): String? = catching {
            val obj = MailrJson.parseToJsonElement(text) as? JsonObject ?: return null
            (obj["code"] as? JsonPrimitive)?.takeIf { it.isString }?.content?.takeIf { it.isNotBlank() }
        }.getOrNull()

        /** The error's message values (`"params"`), each as text; empty when the body has none. */
        fun paramsOf(text: String): Map<String, String> = catching {
            val obj = MailrJson.parseToJsonElement(text) as? JsonObject ?: return emptyMap()
            val p = obj["params"] as? JsonObject ?: return emptyMap()
            p.mapNotNull { (k, v) ->
                when (v) {
                    is JsonNull -> null
                    is JsonPrimitive -> k to v.content
                    else -> k to v.toString()
                }
            }.toMap()
        }.getOrNull() ?: emptyMap()
    }
}

/**
 * A shared file went past the attachment limit while it was being sent. A ProtocolException,
 * so OkHttp does not retry the upload (a retry would read the stream again).
 */
class UploadTooLargeException(val name: String) : java.net.ProtocolException("$name is over 35 MB, too big to attach.") // i18n-ignore: log text; userMessage shows the resource

/** The server's text for this error in the app's language ([ServerMessages]), else its English detail. */
fun ApiException.shownDetail(): String = ServerMessages.text(messageCode, params) ?: detail

/**
 * A short, human message for any failure (network or server). A server error with a message code
 * shows the server's text in the app's language ([ServerMessages]); the rest keep the app's own texts.
 */
fun Throwable.userMessage(): String = when (this) {
    is UploadTooLargeException -> AppText.get(R.string.session_error_upload_too_large, name)
    is ApiException -> ServerMessages.text(messageCode, params) ?: when (code) {
        401 -> AppText.get(R.string.session_error_signed_out)
        403 -> detail.ifBlank { AppText.get(R.string.session_error_not_allowed) }
        404 -> AppText.get(R.string.session_error_not_found)
        426 -> AppText.get(R.string.session_error_upgrade_required)
        429 -> AppText.get(R.string.session_error_too_many_requests)
        in 500..599 -> AppText.get(R.string.session_error_server)
        else -> detail
    }
    is IOException -> AppText.get(R.string.session_error_offline)
    else -> message ?: AppText.get(R.string.session_error_generic)
}
