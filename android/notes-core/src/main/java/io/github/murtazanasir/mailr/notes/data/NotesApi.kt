package io.github.murtazanasir.mailr.notes.data

import io.github.murtazanasir.mailr.notes.R
import io.github.murtazanasir.mailr.i18n.AppText
import kotlinx.serialization.KSerializer
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import java.io.IOException

/**
 * The transport the host gives the Notes module: requests to `/api/v1/<path>` on the signed-in
 * server, with the host's session, CSRF token and `X-Mailr-Client` header (so the server's change
 * events for this install's own writes come back with [clientId] as `origin`). The mailr app passes
 * its ApiClient; the Notes app passes its own.
 */
interface NotesHttp {
    val clientId: String

    /** The answer's body as text. Throws [NotesHttpException] for an error status, an IOException offline. */
    suspend fun request(method: String, path: String, body: String? = null, idempotencyKey: String? = null,
                        query: Map<String, String> = emptyMap()): String

    /** A GET whose answer is not JSON (a Markdown or CSV export), as text. */
    suspend fun text(path: String, query: Map<String, String> = emptyMap()): String = request("GET", path, query = query)
}

/** The server answered with an error status; [body] is the raw answer (a 409 carries the server copy). */
/** [shown] is the server's text for a coded error in the app's language, when the transport has it. */
class NotesHttpException(val code: Int, val detail: String, val body: String? = null, val shown: String? = null) : Exception(detail) {
    val retryable: Boolean get() = code == 408 || code == 429 || code >= 500
    val conflict: Boolean get() = code == 409
}

/** A short message for a failure, in the app's words. */
fun Throwable.notesMessage(): String = when (this) {
    is NotesHttpException -> shown ?: when (code) {
        403 -> detail.ifBlank { AppText.get(R.string.notes_error_not_allowed) }
        404 -> AppText.get(R.string.notes_error_not_found)
        429 -> AppText.get(R.string.notes_error_too_many_requests)
        in 500..599 -> AppText.get(R.string.notes_error_server)
        else -> detail
    }
    is IOException -> AppText.get(R.string.notes_error_offline)
    else -> message ?: AppText.get(R.string.notes_error_generic)
}

/** The notes and tables routes the phone uses. */
class NotesApi(val http: NotesHttp) {
    private val json = NotesJson

    private suspend fun <T> get(path: String, ser: KSerializer<T>, query: Map<String, String> = emptyMap()): T =
        json.decodeFromString(ser, http.request("GET", path, query = query).ifBlank { "{}" })

    suspend fun raw(method: String, path: String, body: JsonElement? = null, key: String? = null): JsonObject {
        val text = http.request(method, path, body?.toString(), key)
        return json.parseToJsonElement(text.ifBlank { "{}" }) as? JsonObject ?: JsonObject(emptyMap())
    }

    suspend fun notesSync(since: String?): NotesDelta =
        get("notes/sync", NotesDelta.serializer(), since?.let { mapOf("since" to it) }.orEmpty())

    suspend fun tablesSync(since: String?): TablesDelta =
        get("tables/sync", TablesDelta.serializer(), since?.let { mapOf("since" to it) }.orEmpty())

    suspend fun note(id: String): Note = get("notes/$id", Note.serializer())

    suspend fun labels(): List<Label> {
        val list = raw("GET", "notes/labels")["labels"]?.jsonArray ?: return emptyList()
        return json.decodeFromJsonElement(ListSerializer(Label.serializer()), list)
    }

    /** Cards of a view the phone does not keep (Trash): `GET /notes?view=…`. */
    suspend fun view(view: String): Pair<List<Note>, List<DataTable>> {
        val o = json.parseToJsonElement(http.request("GET", "notes", query = mapOf("view" to view))).jsonObject
        val notes = o["notes"]?.let { json.decodeFromJsonElement(ListSerializer(Note.serializer()), it) }.orEmpty()
        val tables = o["tables"]?.let { json.decodeFromJsonElement(ListSerializer(DataTable.serializer()), it) }.orEmpty()
        return notes to tables
    }

    /** `GET /notes/search` (note.search): notes and tables. */
    suspend fun search(q: String): Pair<List<Note>, List<DataTable>> {
        val o = json.parseToJsonElement(http.request("GET", "notes/search", query = mapOf("q" to q))).jsonObject
        val notes = o["notes"]?.let { json.decodeFromJsonElement(ListSerializer(Note.serializer()), it) }.orEmpty()
        val tables = o["tables"]?.let { json.decodeFromJsonElement(ListSerializer(DataTable.serializer()), it) }.orEmpty()
        return notes to tables
    }

    suspend fun usual(id: String): Usual = get("notes/$id/usual", Usual.serializer())

    suspend fun revisions(id: String): List<Revision> {
        val list = raw("GET", "notes/$id/revisions")["revisions"]?.jsonArray ?: return emptyList()
        return json.decodeFromJsonElement(ListSerializer(Revision.serializer()), list)
    }

    suspend fun revision(id: String, rev: Long): Revision = get("notes/$id/revisions/$rev", Revision.serializer())

    suspend fun templates(): List<Template> {
        val list = raw("GET", "notes/templates")["templates"]?.jsonArray ?: return emptyList()
        return json.decodeFromJsonElement(ListSerializer(Template.serializer()), list)
    }

    suspend fun table(id: String): DataTable = get("tables/$id", DataTable.serializer())

    suspend fun row(table: String, row: String): Row = get("tables/$table/rows/$row", Row.serializer())

    suspend fun rows(table: String, view: String?, spec: JsonObject? = null, q: String? = null): RowsPage =
        get("tables/$table/rows", RowsPage.serializer(), buildMap {
            view?.let { put("view", it) }
            spec?.let { put("spec", it.toString()) }
            q?.takeIf { it.isNotBlank() }?.let { put("q", it) }
            put("limit", "2000")
        })

    suspend fun rowHistory(table: String, row: String): RowHistory = get("tables/$table/rows/$row/changes", RowHistory.serializer())

    /** ACT note.export: one note as Markdown (`GET /notes/{id}/export`). */
    suspend fun exportNote(id: String): String = http.text("notes/$id/export")

    /** ACT table.export_csv: the table's default view as CSV (`GET /tables/{id}/export.csv`). */
    suspend fun exportCsv(id: String): String = http.text("tables/$id/export.csv")

    suspend fun shares(kind: String, id: String): ShareDialog = get("${base(kind)}/$id/shares", ShareDialog.serializer())

    suspend fun share(kind: String, id: String, principal: String, level: String): ShareDialog =
        json.decodeFromString(ShareDialog.serializer(),
            http.request("PUT", "${base(kind)}/$id/shares/$principal", buildJsonObject { put("level", level) }.toString()))

    suspend fun unshare(kind: String, id: String, principal: String): ShareDialog =
        json.decodeFromString(ShareDialog.serializer(), http.request("DELETE", "${base(kind)}/$id/shares/$principal"))

    companion object {
        fun base(kind: String) = if (kind == "table") "tables" else "notes"
    }
}
