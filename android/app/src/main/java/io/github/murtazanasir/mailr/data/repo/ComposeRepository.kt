package io.github.murtazanasir.mailr.data.repo

import io.github.murtazanasir.mailr.util.catching

import android.content.ContentResolver
import android.net.Uri
import io.github.murtazanasir.mailr.data.api.CancelResponse
import io.github.murtazanasir.mailr.data.api.ComposeAttachment
import io.github.murtazanasir.mailr.data.api.ComposePayload
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.Outbox
import io.github.murtazanasir.mailr.data.api.Person
import io.github.murtazanasir.mailr.data.api.SendRequest
import java.util.UUID

class ComposeRepository(private val api: MailrApi, private val mail: MailRepository) {

    suspend fun prefill(messageId: Long, mode: String): ComposePayload = api.replyPrefill(messageId, mode)

    suspend fun loadDraft(id: Long): ComposePayload = api.draft(id).payload

    /** The draft with its live version (live editing: the composer's base). */
    suspend fun loadDraftDetail(id: Long): io.github.murtazanasir.mailr.data.api.DraftDetail = api.draft(id)

    /** Save (create or replace) a draft. Returns the draft's message id. */
    suspend fun saveDraft(payload: ComposePayload, draftId: Long?): Long =
        api.saveDraft(payload, draftId, UUID.randomUUID().toString()).draft.id

    suspend fun discardDraft(id: Long) {
        catching { api.deleteDraft(id) }
        mail.invalidate()
    }

    /**
     * Queue a send. The server holds it for the undo window (or until [sendAt]). [key] is the send
     * attempt's Idempotency-Key: the same key again (a retry after a lost answer) gets the first
     * request's result instead of a second copy.
     */
    suspend fun send(payload: ComposePayload, draftId: Long?, sendAt: String?, key: String = UUID.randomUUID().toString()): Outbox {
        val ob = api.send(SendRequest(payload, draftId, sendAt), key).outbox
        mail.invalidate()
        return ob
    }

    suspend fun cancel(outboxId: Long): CancelResponse = api.cancelOutbox(outboxId).also { mail.invalidate() }

    suspend fun suggest(q: String): List<Person> = if (q.length < 2) emptyList() else catching { api.people(q) }.getOrDefault(emptyList())

    suspend fun upload(resolver: ContentResolver, uri: Uri): ComposeAttachment {
        val u = api.upload(resolver, uri)
        return ComposeAttachment(u.blob, u.filename, u.type, u.size)
    }

    /** ACT mail.insert_image @android.editor: an inline photo goes up through /uploads, as on the web. */
    suspend fun uploadInline(bytes: ByteArray, name: String, type: String): String = api.uploadBytes(bytes, name, type).blob

    /** An inline image of a draft, from the user's uploads (GET /uploads/<blob>), to show it. */
    suspend fun inlineImage(blob: String): ByteArray? = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
        catching { api.proxiedImage("/api/v1/uploads/$blob")?.second }.getOrNull()
    }
}
