package io.github.murtazanasir.mailr.data.share

import io.github.murtazanasir.mailr.util.catching

import android.content.ContentResolver
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.ComposeAttachment
import io.github.murtazanasir.mailr.data.api.ComposePayload
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.SendRequest
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.db.SelfSendDao
import io.github.murtazanasir.mailr.data.db.SelfSendEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.time.OffsetDateTime
import java.util.UUID
import java.util.concurrent.TimeUnit
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/** A shared file copied into app storage (the sender's read grant ends with the share). */
@Serializable
data class StagedFile(val path: String, val name: String, val type: String, val size: Long, val blob: String? = null)

object SelfState {
    /** Staged, waiting for the user's Send tap: a share never sends without one. */
    const val CONFIRM = "CONFIRM"
    const val QUEUED = "QUEUED"
    const val EDITING = "EDITING"
    const val SENDING = "SENDING"
    const val HELD = "HELD"
    const val CANCELLED = "CANCELLED"
    const val FAILED = "FAILED"
}

class TooLargeException(val name: String) : Exception("$name is over 35 MB")

/** What Undo (or pausing for a note) achieved. */
enum class UndoResult { STOPPED, TOO_LATE, OFFLINE, GONE }

/**
 * "Send to self": stage the share, wait for the user's Send tap, queue it, and send it to the
 * user's own default identity. Another app can start the share card with any content, so a
 * share is never queued until the person taps Send ([confirm]).
 *
 * The row in `self_sends` is the source of truth. The worker claims a row with a
 * compare-and-set (QUEUED → SENDING) right before POST /send, and Undo stops a row with
 * QUEUED/EDITING → CANCELLED, so exactly one of them wins. Once the server holds the message
 * (HELD) Undo cancels it there, within the user's undo-send window.
 */
class SelfSendRepository(
    private val filesDir: File,
    private val api: MailrApi,
    private val dao: SelfSendDao,
    private val titleOf: suspend (String) -> String? = ::fetchPageTitle,
    private val now: () -> Long = System::currentTimeMillis,  // clock-ok: undo window and pruning
) {
    /** Set by the app graph: schedule the worker for this row. */
    var enqueue: (String) -> Unit = {}

    /** Set by the app graph: tell the user a send failed for good. */
    var onFailed: (SelfSendEntity) -> Unit = {}

    /** Set by the app graph: a send reached the server (ranks the sharing shortcut). */
    var onSent: () -> Unit = {}

    fun watch(id: String): Flow<SelfSendEntity?> = dao.watch(id)
    suspend fun get(id: String): SelfSendEntity? = dao.get(id)

    /**
     * Copy the shared files and store the share, waiting for [confirm] (or, with [startEditing],
     * for a note). Nothing is queued here. Returns the row id.
     */
    suspend fun create(shared: SharedContent, resolver: ContentResolver, startEditing: Boolean = false): String {
        val id = UUID.randomUUID().toString()
        val files = stage(id, shared.files, resolver)
        dao.insert(SelfSendEntity(
            id = id, sendKey = UUID.randomUUID().toString(), subject = "",
            sharedSubject = shared.subject, sharedTitle = shared.title,
            text = shared.text ?: shared.html?.let { htmlToText(it) }.orEmpty(),
            filesJson = MailrJson.encodeToString(FILES, files),
            state = if (startEditing) SelfState.EDITING else SelfState.CONFIRM, createdAt = now(),
        ))
        return id
    }

    /**
     * The user tapped Send on the card: queue it, with what the card holds. A blank [subject]
     * leaves the subject to the worker (it may fetch a page title); [labelIds] are the user's own
     * labels, kept on the row so a send queued offline still carries them. False when the share
     * was no longer waiting.
     */
    suspend fun confirm(id: String, subject: String = "", note: String = "", labelIds: Collection<Long> = emptyList()): Boolean {
        if (dao.confirm(id, subject.trim(), note.trim(), encodeLabels(labelIds)) == 0) return false
        enqueue(id)
        return true
    }

    private suspend fun stage(id: String, uris: List<android.net.Uri>, resolver: ContentResolver): List<StagedFile> =
        withContext(Dispatchers.IO) {
            if (uris.isEmpty()) return@withContext emptyList()
            val dir = File(filesDir, "self-send/$id").apply { mkdirs() }
            try {
                uris.mapIndexed { i, uri ->
                    val meta = resolver.fileMeta(uri)
                    if (meta.size > MAX_BYTES) throw TooLargeException(meta.name)
                    ensureSpace(dir, if (meta.size > 0) meta.size else MAX_BYTES)
                    val out = File(dir, "$i-${meta.name}")
                    // The provider's size may be missing or wrong: the copy itself stops past the limit.
                    val copied = resolver.openInputStream(uri)?.use { input ->
                        out.outputStream().use { o -> copyCapped(input, o, MAX_BYTES) }
                    } ?: throw IOException(AppText.get(R.string.sharing_cant_read_file, meta.name))
                    if (copied > MAX_BYTES) throw TooLargeException(meta.name)
                    StagedFile(out.absolutePath, meta.name, meta.type, copied)
                }
            } catch (e: Exception) {
                dir.deleteRecursively()
                throw e
            }
        }

    enum class Outcome { DONE, RETRY }

    /** One worker run for [id]: upload, name, claim, send. */
    suspend fun process(id: String): Outcome {
        prune()
        val e = dao.get(id) ?: return Outcome.DONE
        if (e.state != SelfState.QUEUED) return Outcome.DONE
        return try {
            // 1. Upload what is not on the server yet (kept across retries).
            val files = decodeFiles(e.filesJson).toMutableList()
            for (i in files.indices) {
                if (files[i].blob != null) continue
                val f = files[i]
                files[i] = f.copy(blob = api.uploadFile(File(f.path), f.name, f.type).blob)
                dao.setFiles(id, MailrJson.encodeToString(FILES, files))
            }
            // 2. A subject, fetching the page title when all we have is a bare link.
            var subject = e.subject
            if (subject.isBlank()) {
                val page = if (e.sharedSubject.isNullOrBlank() && e.sharedTitle.isNullOrBlank() && SelfSubject.isLoneUrl(e.text))
                    catching { titleOf(e.text.trim()) }.getOrNull() else null
                subject = SelfSubject.derive(e.sharedSubject, e.sharedTitle, page, e.text, files.map { it.name }, e.note)
                dao.setSubject(id, subject)
            }
            // 3. The user's own primary address.
            val me = api.me()
            val ident = me.identities.firstOrNull { it.isDefault } ?: me.identities.firstOrNull()
            val to = ident?.let { Address(it.email, it.name.ifBlank { null }) } ?: Address(me.user.email, me.user.name.ifBlank { null })
            // 4. Claim, then send. Undo can no longer stop it locally, only on the server.
            if (dao.transition(id, SelfState.QUEUED, SelfState.SENDING) == 0) return Outcome.DONE
            val fresh = dao.get(id) ?: return Outcome.DONE
            val payload = ComposePayload(
                identityId = ident?.id, to = listOf(to), subject = subject,
                text = SelfSubject.body(fresh.text, fresh.note, subject).ifBlank { subject },
                attachments = files.map { ComposeAttachment(it.blob ?: throw IllegalStateException("a file was not uploaded"), it.name, it.type, it.size) },
                // The server treats a share as content from another app, never as the user's own
                // words (assistant, triage, standing orders).
                headers = mapOf("X-Mailr-Origin" to "android-share"),
                // ACT mail.send_to_self_labels @android.share: the labels picked on the card.
                labelIds = decodeLabels(fresh.labelIds).ifEmpty { null },
            )
            val ob = try {
                api.send(SendRequest(payload), fresh.sendKey).outbox
            } catch (x: Exception) {
                dao.transition(id, SelfState.SENDING, SelfState.QUEUED)
                throw x
            }
            dao.held(id, ob.id, ob.sendAt?.let { catching { OffsetDateTime.parse(it).toInstant().toEpochMilli() }.getOrNull() })
            deleteFiles(id)
            onSent()
            Outcome.DONE
        } catch (x: IOException) {
            Outcome.RETRY
        } catch (x: ApiException) {
            when {
                x.code == 400 && "expired" in x.detail -> {
                    // The server dropped our uploads; send them again.
                    dao.setFiles(id, MailrJson.encodeToString(FILES, decodeFiles(e.filesJson).map { it.copy(blob = null) }))
                    Outcome.RETRY
                }
                x.retryable && e.attempts < 12 -> { dao.setState(id, SelfState.QUEUED, x.detail); Outcome.RETRY }
                else -> {
                    dao.setState(id, SelfState.FAILED, x.userMessage())
                    dao.get(id)?.let(onFailed)
                    Outcome.DONE
                }
            }
        }
    }

    /** Undo: stop it here if it has not left, else cancel the held message on the server. */
    suspend fun cancel(id: String): UndoResult = stop(id, SelfState.CANCELLED).also {
        if (it == UndoResult.STOPPED) { deleteFiles(id) }
    }

    /** "Add a note": pause (or pull back from the server) so the note can go in first. */
    suspend fun pauseForNote(id: String): UndoResult = stop(id, SelfState.EDITING)

    private suspend fun stop(id: String, target: String): UndoResult {
        if (dao.transition(id, SelfState.CONFIRM, target) == 1) return UndoResult.STOPPED
        if (dao.transition(id, SelfState.QUEUED, target) == 1) return UndoResult.STOPPED
        if (target != SelfState.EDITING && dao.transition(id, SelfState.EDITING, target) == 1) return UndoResult.STOPPED
        if (target == SelfState.EDITING && dao.get(id)?.state == SelfState.EDITING) return UndoResult.STOPPED
        // In flight: wait for the POST to land (or fail back to QUEUED).
        val cur = dao.get(id) ?: return UndoResult.GONE
        val e = if (cur.state != SelfState.SENDING) cur
        else withTimeoutOrNull(20_000) { dao.watch(id).first { it == null || it.state != SelfState.SENDING } }
            ?: return UndoResult.OFFLINE
        return when (e.state) {
            SelfState.QUEUED -> stop(id, target)
            SelfState.HELD -> try {
                api.cancelOutbox(e.outboxId ?: return UndoResult.GONE)
                // The uploaded blobs stay on the server, so an edited copy re-sends with them.
                dao.setState(id, target, null)
                UndoResult.STOPPED
            } catch (x: ApiException) {
                UndoResult.TOO_LATE
            } catch (x: IOException) {
                UndoResult.OFFLINE
            }
            SelfState.CANCELLED, SelfState.EDITING -> UndoResult.STOPPED
            else -> UndoResult.GONE
        }
    }

    /**
     * Send (again) with the note the user typed; a new idempotency key, since it is a new send.
     * [labelIds] replaces the labels picked before; null keeps them.
     */
    suspend fun sendWithNote(id: String, note: String, labelIds: Collection<Long>? = null) {
        val e = dao.get(id) ?: return
        if (e.state != SelfState.EDITING && e.state != SelfState.FAILED) return
        labelIds?.let { dao.setLabels(id, encodeLabels(it)) }
        dao.requeue(id, note.trim(), UUID.randomUUID().toString())
        if (e.sharedSubject.isNullOrBlank() && e.sharedTitle.isNullOrBlank() && e.text.isBlank() &&
            decodeFiles(e.filesJson).isEmpty()) {
            // A plain note: its (new) first line is the subject.
            dao.setSubject(id, "")
        }
        enqueue(id)
    }

    /** Retry after a permanent-looking failure (from the notification). */
    suspend fun retry(id: String) {
        val e = dao.get(id) ?: return
        if (e.state == SelfState.FAILED) {
            dao.requeue(id, e.note, UUID.randomUUID().toString())
            enqueue(id)
        }
    }

    /** Drop finished rows and their files: held ones once the undo window is long past. */
    suspend fun prune() {
        val t = now()
        dao.all().forEach { e ->
            val done = when (e.state) {
                SelfState.HELD -> (e.sendAt ?: e.createdAt) < t - 60 * 60_000
                SelfState.CANCELLED -> e.createdAt < t - 10 * 60_000
                // Never confirmed (the card went away without a tap).
                SelfState.CONFIRM -> e.createdAt < t - 60 * 60_000
                else -> e.createdAt < t - 7 * 24 * 60 * 60_000L
            }
            if (done) {
                deleteFiles(e.id)
                dao.delete(e.id)
            }
        }
    }

    private fun deleteFiles(id: String) {
        File(filesDir, "self-send/$id").deleteRecursively()
    }

    companion object {
        const val MAX_BYTES = 35L * 1024 * 1024

        /** Room left on the phone after a staged copy. */
        private const val SPARE_BYTES = 64L * 1024 * 1024

        /**
         * Copy at most [limit] + 1 bytes, so the caller sees "too large" without the rest of the
         * stream ever being read. Returns the bytes written.
         */
        fun copyCapped(input: java.io.InputStream, out: java.io.OutputStream, limit: Long): Long {
            val buf = ByteArray(64 * 1024)
            var total = 0L
            while (total <= limit) {
                val n = input.read(buf, 0, minOf(buf.size.toLong(), limit + 1 - total).toInt())
                if (n < 0) break
                out.write(buf, 0, n)
                total += n
            }
            return total
        }

        /** Refuse a copy that would leave the phone short of space (only when the size of the volume is known). */
        private fun ensureSpace(dir: File, needed: Long) {
            val stat = catching { android.os.StatFs(dir.absolutePath) }.getOrNull() ?: return
            if (stat.totalBytes <= 0) return
            if (stat.availableBytes < needed + SPARE_BYTES) throw IOException(AppText.get(R.string.sharing_no_space))
        }
        val FILES = ListSerializer(StagedFile.serializer())

        fun encodeLabels(ids: Collection<Long>): String = ids.distinct().joinToString(",")

        fun decodeLabels(s: String): List<Long> = s.split(',').mapNotNull { it.trim().toLongOrNull() }.distinct()

        fun decodeFiles(json: String): List<StagedFile> =
            catching { MailrJson.decodeFromString(FILES, json) }.getOrDefault(emptyList())

        private fun htmlToText(html: String) = io.github.murtazanasir.mailr.ui.util.Format.htmlToText(html)

        /**
         * The page-title client: no session cookie, https only, and only to public addresses:
         * [PublicDns] refuses a name that resolves to the phone's own network, so a shared link
         * cannot make the phone fetch from the LAN. Redirects are followed by [fetchPageTitle]
         * itself, so every hop passes the same https, port 443 and public-host rule.
         */
        private val plain by lazy {
            OkHttpClient.Builder().callTimeout(6, TimeUnit.SECONDS).followRedirects(false).followSslRedirects(false)
                .dns(PublicDns).addNetworkInterceptor(PublicDns.connectionGuard).build()
        }

        /**
         * The page's title, for links shared without one: only https links to a public host on
         * the default port. Anything else gets no title (the subject falls back to the link).
         */
        suspend fun fetchPageTitle(url: String): String? = withContext(Dispatchers.IO) {
            if (!PublicDns.fetchable(url)) return@withContext null
            var current = url
            repeat(MAX_TITLE_HOPS) {
                val req = Request.Builder().url(current).header("Accept", "text/html")
                    .header("User-Agent", "Mozilla/5.0 (Linux; Android) mailr").get().build()
                val next: String? = plain.newCall(req).execute().use { r ->
                    if (r.isRedirect) return@use PublicDns.redirectTarget(current, r.header("Location")) ?: ""
                    val type = r.header("Content-Type").orEmpty()
                    if (!r.isSuccessful || (type.isNotEmpty() && "html" !in type)) return@withContext null
                    val src = r.body?.source() ?: return@withContext null
                    src.request(256 * 1024)
                    return@withContext SelfSubject.titleFromHtml(src.buffer.snapshot().utf8())
                }
                if (next.isNullOrEmpty()) return@withContext null
                current = next
            }
            null
        }

        /** Redirects followed for a page title, each one checked. */
        private const val MAX_TITLE_HOPS = 5
    }
}
