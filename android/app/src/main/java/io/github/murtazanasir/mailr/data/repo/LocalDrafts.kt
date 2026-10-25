package io.github.murtazanasir.mailr.data.repo

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import io.github.murtazanasir.mailr.MailrApp
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.ComposePayload
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.db.LocalDraftDao
import io.github.murtazanasir.mailr.data.db.LocalDraftEntity
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.io.IOException
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit

/**
 * The composer's local copies: every edit lands in Room first, then goes to the server
 * as the draft. A copy the server does not have yet survives Back, process death and a missing
 * network; [LocalDraftWorker] sends it once the phone is online.
 *
 * One lock covers every server save, so the composer and the worker never both create the same
 * draft. Each local edit bumps the row's version; a save marks only the version it sent as synced.
 */
class LocalDrafts(private val api: MailrApi, private val dao: LocalDraftDao) {
    private val lock = Mutex()

    /** Composers open now: their rows stay while they are open, synced or not. */
    private val open: MutableSet<String> = ConcurrentHashMap.newKeySet()

    /** Set by the app: schedules the worker for a copy the server does not have. */
    var onUnsynced: (() -> Unit)? = null

    val all: Flow<List<LocalDraftEntity>> = dao.all()

    sealed interface SaveResult {
        /**
         * The server has it. [version] is the draft's live version; [merged]: the server merged the
         * save with changes saved since [sent]'s base, and [stored] is what it keeps.
         */
        data class Saved(
            val draftId: Long,
            val version: Int? = null,
            val merged: Boolean = false,
            val stored: kotlinx.serialization.json.JsonObject? = null,
            val sent: ComposePayload? = null,
        ) : SaveResult
        /** The draft changed since the base version where this save changed it too (409, core/live.py): kept here. */
        data class Conflict(val body: kotlinx.serialization.json.JsonObject) : SaveResult
        /** Kept on this phone. [error] is null when the phone is offline; [retry]: the worker tries again. */
        data class Local(val error: String?, val retry: Boolean = true) : SaveResult
        /** The session ended (401), or this app version is too old for the server (426): kept, and synced after sign-in or the update. */
        data object Paused : SaveResult
    }

    fun newId(): String = UUID.randomUUID().toString()

    fun opened(id: String) { open += id }
    fun released(id: String) { open -= id }

    suspend fun get(id: String): LocalDraftEntity? = dao.get(id)

    suspend fun payloadOf(e: LocalDraftEntity): ComposePayload =
        MailrJson.decodeFromString(ComposePayload.serializer(), e.payload)

    /** The composer's state into Room (never touches the network). Returns the new version. */
    suspend fun write(id: String, payload: ComposePayload, draftId: Long?): Long {
        val now = System.currentTimeMillis()  // clock-ok: local draft bookkeeping
        val old = dao.get(id)
        val version = (old?.version ?: 0) + 1
        dao.put(LocalDraftEntity(id = id, draftId = old?.draftId ?: draftId,
            payload = MailrJson.encodeToString(ComposePayload.serializer(), payload), subject = payload.subject,
            version = version, syncedVersion = old?.syncedVersion ?: 0, closed = false, error = old?.error, updatedAt = now))
        return version
    }

    /** The row starts out as what the server already has (a draft opened from Drafts). */
    suspend fun adopt(id: String, payload: ComposePayload, draftId: Long) {
        if (dao.get(id) != null) return
        dao.put(LocalDraftEntity(id = id, draftId = draftId, payload = MailrJson.encodeToString(ComposePayload.serializer(), payload),
            subject = payload.subject, version = 1, syncedVersion = 1, updatedAt = System.currentTimeMillis()))  // clock-ok: local draft bookkeeping
    }

    /**
     * Send the row's current version to the server as its draft (create or replace). An open
     * composer passes its [baseVersion] (live editing): the server merges or refuses (Conflict);
     * without one (the worker) the copy replaces the draft, as before live editing.
     */
    suspend fun sync(id: String, baseVersion: Int? = null): SaveResult = lock.withLock {
        val row = dao.get(id) ?: return@withLock SaveResult.Local(null)
        if (row.synced && row.draftId != null) return@withLock SaveResult.Saved(row.draftId).also { dropIfDone(id) }
        val payload = payloadOf(row)
        try {
            val r = save(payload, row, baseVersion)
            dao.synced(id, r.draft.id, row.version)
            dropIfDone(id)
            SaveResult.Saved(r.draft.id, r.version, r.merged, r.payload, payload)
        } catch (e: LiveRefused) {
            SaveResult.Conflict(e.body)
        } catch (e: IOException) {
            onUnsynced?.invoke()
            SaveResult.Local(null)
        } catch (e: ApiException) {
            // A 401 waits for sign-in; a 426 (this app version is too old) waits for an update.
            if (e.code == 401 || e.code == 426) return@withLock SaveResult.Paused
            dao.setError(id, e.userMessage())
            if (e.retryable) onUnsynced?.invoke()
            SaveResult.Local(e.userMessage(), retry = e.retryable)
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            // An answer the app could not read: the copy stays here and is tried again later.
            dao.setError(id, e.userMessage())
            onUnsynced?.invoke()
            SaveResult.Local(e.userMessage(), retry = true)
        }
    }

    /**
     * One save. The key follows the row and its version, so a save retried after a lost answer
     * is applied once. A draft deleted elsewhere (404) is created again; a key the server can no
     * longer replay (409) is replaced once by a fresh one.
     */
    private suspend fun save(payload: ComposePayload, row: LocalDraftEntity, baseVersion: Int?): io.github.murtazanasir.mailr.data.api.DraftResponse {
        val key = "draft-${row.id}-v${row.version}-${row.draftId ?: "new"}"
        return try {
            api.saveDraft(payload, row.draftId, key, baseVersion)
        } catch (e: ApiException) {
            liveConflict(e)?.let { throw LiveRefused(it) }
            when {
                e.code == 404 && row.draftId != null -> api.saveDraft(payload, null, "$key-again")
                e.code == 409 -> try {
                    api.saveDraft(payload, row.draftId, UUID.randomUUID().toString(), baseVersion)
                } catch (again: ApiException) {
                    throw liveConflict(again)?.let { LiveRefused(it) } ?: again
                }
                else -> throw e
            }
        }
    }

    private class LiveRefused(val body: kotlinx.serialization.json.JsonObject) : Exception("live conflict")

    /** The body of a live-editing 409 (`"conflict": true`), else null (a key the server cannot replay is 409 too). */
    private fun liveConflict(e: ApiException): kotlinx.serialization.json.JsonObject? {
        if (e.code != 409) return null
        val o = e.body?.let { catching { MailrJson.parseToJsonElement(it) as? kotlinx.serialization.json.JsonObject }.getOrNull() } ?: return null
        return o.takeIf { (it["conflict"] as? kotlinx.serialization.json.JsonPrimitive)?.content == "true" }
    }

    /**
     * Every copy the server lacks. False when the worker should try again (offline, or a server
     * failure); a refusal stays in Pending changes, and a 401 waits for sign-in.
     */
    suspend fun syncAll(): Boolean {
        var again = false
        for (row in dao.unsynced()) {
            when (val r = sync(row.id)) {
                is SaveResult.Saved, is SaveResult.Conflict -> Unit
                SaveResult.Paused -> return true
                is SaveResult.Local -> if (r.error == null) return false else again = again || r.retry
            }
        }
        dropStale()
        return !again
    }

    /** The composer closed: the row goes once the server has its last version. */
    suspend fun close(id: String) {
        released(id)
        dao.close(id)
        dropIfDone(id)
    }

    /** Discard, or the message was sent: nothing of it stays here. */
    suspend fun delete(id: String) {
        released(id)
        dao.delete(id)
    }

    private suspend fun dropIfDone(id: String) {
        val row = dao.get(id) ?: return
        if (row.synced && row.closed) dao.delete(id)
    }

    /** Synced copies no composer holds, left behind when the app was closed with a composer open. */
    private suspend fun dropStale() {
        val day = System.currentTimeMillis() - 24 * 3600_000L  // clock-ok: local draft bookkeeping
        dao.allNow().filter { it.synced && it.id !in open && (it.closed || it.updatedAt < day) }.forEach { dao.delete(it.id) }
    }

    suspend fun clear() = dao.clear()
}

object LocalDraftWork {
    fun enqueue(context: Context) {
        WorkManager.getInstance(context).enqueueUniqueWork(
            "local-drafts", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<LocalDraftWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS).build(),
        )
    }
}

/** Sends the composer's local copies the server does not have yet. */
class LocalDraftWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val g = (applicationContext as MailrApp).graph
        if (g.sessions.session.value == null) return Result.success()  // after sign-in
        return if (g.drafts.syncAll()) Result.success() else Result.retry()
    }
}
