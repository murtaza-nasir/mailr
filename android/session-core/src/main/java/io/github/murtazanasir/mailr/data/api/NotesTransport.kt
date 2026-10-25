package io.github.murtazanasir.mailr.data.api

import io.github.murtazanasir.mailr.notes.data.NotesHttp
import io.github.murtazanasir.mailr.notes.data.NotesHttpException
import kotlinx.serialization.json.JsonElement
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.UUID

/**
 * The Notes module's transport (android/notes-core) on this app's [ApiClient]: the same session
 * cookie, CSRF token, plain-HTTP guard and `X-Mailr-Client` id as every other request, and the
 * same sign-out on a 401.
 */
class NotesTransport(private val client: ApiClient) : NotesHttp {
    override val clientId: String get() = client.clientId

    override suspend fun request(method: String, path: String, body: String?, idempotencyKey: String?,
                                 query: Map<String, String>): String {
        val b = Request.Builder().url(client.url(path, query))
        if (method == "GET") b.get()
        else {
            b.method(method, (body ?: "{}").toRequestBody(ApiClient.JSON_TYPE))
            b.header("Idempotency-Key", idempotencyKey ?: UUID.randomUUID().toString())
        }
        return try {
            client.execute(b.build(), JsonElement.serializer()).toString()
        } catch (e: ApiException) {
            throw NotesHttpException(e.code, e.detail, e.body, ServerMessages.text(e.messageCode, e.params))
        }
    }

    override suspend fun text(path: String, query: Map<String, String>): String {
        val r = client.raw(Request.Builder().url(client.url(path, query)).get().build())
        r.use { resp ->
            val text = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) { resp.body?.string().orEmpty() }
            if (!resp.isSuccessful) throw NotesHttpException(resp.code, ApiClient.detailOf(text) ?: "HTTP ${resp.code}", text,
                ServerMessages.text(ApiClient.messageCodeOf(text), ApiClient.paramsOf(text)))
            return text
        }
    }
}
