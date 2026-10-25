package io.github.murtazanasir.mailr.data.db

import android.content.Context
import androidx.paging.PagingSource
import androidx.room.AutoMigration
import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Index
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.Transaction
import androidx.room.Upsert
import androidx.room.migration.Migration
import kotlinx.coroutines.flow.Flow

/**
 * One row per (view, conversation) in list order. The summary itself is stored as JSON so the
 * cache always matches the server's shape; `starred`/`unread` are columns so optimistic updates
 * are cheap single-row writes.
 */
@Entity(tableName = "list_items", primaryKeys = ["viewKey", "conversationId"],
    indices = [Index(value = ["viewKey", "position"]), Index("conversationId")])
data class ListItemEntity(
    val viewKey: String,
    val conversationId: Long,
    val position: Long,
    val json: String,
)

@Entity(tableName = "remote_keys")
data class RemoteKeyEntity(
    @PrimaryKey val viewKey: String,
    val nextCursor: String?,
    val total: Int?,
    val refreshedAt: Long,
)

@Entity(tableName = "conversations")
data class ConversationEntity(
    @PrimaryKey val id: Long,
    val json: String,
    val fetchedAt: Long,
)

@Entity(tableName = "bodies", primaryKeys = ["messageId", "images"])
data class BodyEntity(
    val messageId: Long,
    val images: Boolean,
    val json: String,
    val fetchedAt: Long,
)

/**
 * A mutation waiting to reach the server (offline or failed). Replayed in order, except that a
 * change the server keeps refusing does not hold back the unrelated ones behind it.
 *
 * [state]: QUEUED (sent when the device is online, not before [nextAttemptAt]) or FAILED (the
 * server refused it, or it kept failing; it waits in Pending changes for Retry or Discard and is
 * never dropped on its own).
 */
@Entity(tableName = "pending_ops")
data class PendingOpEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val idempotencyKey: String,
    val method: String,
    val path: String,
    val body: String,
    val description: String,
    val createdAt: Long,
    val attempts: Int = 0,
    val lastError: String? = null,
    // Version 7.
    @ColumnInfo(defaultValue = "QUEUED") val state: String = PendingState.QUEUED,
    @ColumnInfo(defaultValue = "0") val nextAttemptAt: Long = 0,
)

object PendingState {
    const val QUEUED = "QUEUED"
    const val FAILED = "FAILED"
}

/**
 * The composer's text on this phone (version 7). Written a moment after every edit, before the
 * server hears of it, so Back, process death and a missing network lose nothing. [version]
 * counts local edits; [syncedVersion] is the last one the server has as the draft [draftId].
 * A row the composer has [closed] goes once the server has its last version; until then it
 * shows in Pending changes and [io.github.murtazanasir.mailr.data.repo.LocalDraftWorker] syncs it.
 */
@Entity(tableName = "local_drafts")
data class LocalDraftEntity(
    @PrimaryKey val id: String,
    val draftId: Long? = null,
    /** A [io.github.murtazanasir.mailr.data.api.ComposePayload] as JSON. */
    val payload: String,
    val subject: String = "",
    val version: Long = 1,
    val syncedVersion: Long = 0,
    val closed: Boolean = false,
    val error: String? = null,
    val updatedAt: Long,
) {
    val synced: Boolean get() = syncedVersion >= version
}

@Entity(tableName = "kv")
data class KvEntity(@PrimaryKey val key: String, val value: String)

@Dao
interface ListDao {
    @Query("SELECT * FROM list_items WHERE viewKey = :viewKey ORDER BY position ASC")
    fun paging(viewKey: String): PagingSource<Int, ListItemEntity>

    @Query("SELECT * FROM list_items WHERE viewKey = :viewKey ORDER BY position ASC LIMIT :limit")
    suspend fun first(viewKey: String, limit: Int): List<ListItemEntity>

    @Query("SELECT MAX(position) FROM list_items WHERE viewKey = :viewKey")
    suspend fun maxPosition(viewKey: String): Long?

    @Upsert
    suspend fun upsert(items: List<ListItemEntity>)

    @Query("DELETE FROM list_items WHERE viewKey = :viewKey")
    suspend fun clear(viewKey: String)

    @Query("SELECT * FROM list_items WHERE conversationId IN (:ids)")
    suspend fun rowsFor(ids: List<Long>): List<ListItemEntity>

    /** Cached rows of every other list whose JSON contains [like] (a LIKE pattern), for search offline. */
    @Query("SELECT * FROM list_items WHERE viewKey != :except AND json LIKE :like LIMIT 5000")
    suspend fun cachedMatching(except: String, like: String): List<ListItemEntity>

    @Query("DELETE FROM list_items WHERE viewKey = :viewKey AND conversationId IN (:ids)")
    suspend fun remove(viewKey: String, ids: List<Long>)

    @Query("SELECT * FROM remote_keys WHERE viewKey = :viewKey")
    suspend fun key(viewKey: String): RemoteKeyEntity?

    @Upsert
    suspend fun putKey(key: RemoteKeyEntity)

    @Transaction
    suspend fun replacePage(viewKey: String, items: List<ListItemEntity>, key: RemoteKeyEntity) {
        clear(viewKey)
        upsert(items)
        putKey(key)
    }

    @Transaction
    suspend fun appendPage(items: List<ListItemEntity>, key: RemoteKeyEntity) {
        upsert(items)
        putKey(key)
    }
}

@Dao
interface CacheDao {
    @Query("SELECT * FROM conversations WHERE id = :id")
    suspend fun conversation(id: Long): ConversationEntity?

    @Query("SELECT * FROM conversations WHERE id = :id")
    fun conversationFlow(id: Long): Flow<ConversationEntity?>

    @Upsert
    suspend fun putConversation(e: ConversationEntity)

    @Query("DELETE FROM conversations WHERE id IN (:ids)")
    suspend fun dropConversations(ids: List<Long>)

    @Query("SELECT * FROM bodies WHERE messageId = :id AND images = :images")
    suspend fun body(id: Long, images: Boolean): BodyEntity?

    @Upsert
    suspend fun putBody(e: BodyEntity)

    /** Keep the offline cache bounded: bodies older than the newest [keep] are dropped. */
    @Query("DELETE FROM bodies WHERE fetchedAt < (SELECT MIN(fetchedAt) FROM (SELECT fetchedAt FROM bodies ORDER BY fetchedAt DESC LIMIT :keep))")
    suspend fun trimBodies(keep: Int)

    @Query("DELETE FROM conversations WHERE fetchedAt < (SELECT MIN(fetchedAt) FROM (SELECT fetchedAt FROM conversations ORDER BY fetchedAt DESC LIMIT :keep))")
    suspend fun trimConversations(keep: Int)

    @Query("SELECT value FROM kv WHERE `key` = :key")
    suspend fun get(key: String): String?

    @Query("SELECT value FROM kv WHERE `key` = :key")
    fun watch(key: String): Flow<String?>

    @Upsert
    suspend fun put(kv: KvEntity)
}

@Dao
interface PendingDao {
    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insert(op: PendingOpEntity): Long

    @Query("SELECT * FROM pending_ops ORDER BY id ASC LIMIT :limit")
    suspend fun next(limit: Int): List<PendingOpEntity>

    /** What the drain sends, in order. */
    @Query("SELECT * FROM pending_ops WHERE state = 'QUEUED' ORDER BY id ASC")
    suspend fun queued(): List<PendingOpEntity>

    @Query("SELECT * FROM pending_ops ORDER BY id ASC")
    fun all(): Flow<List<PendingOpEntity>>

    @Query("SELECT * FROM pending_ops WHERE id = :id")
    suspend fun get(id: Long): PendingOpEntity?

    @Query("SELECT COUNT(*) FROM pending_ops")
    fun count(): Flow<Int>

    /** A network failure: noted, not counted against the change. */
    @Query("UPDATE pending_ops SET lastError = :error WHERE id = :id")
    suspend fun noteError(id: Long, error: String)

    /** A server failure worth another try, not before [at]. */
    @Query("UPDATE pending_ops SET attempts = attempts + 1, lastError = :error, nextAttemptAt = :at WHERE id = :id")
    suspend fun retryLater(id: Long, error: String, at: Long)

    @Query("UPDATE pending_ops SET state = 'FAILED', attempts = attempts + 1, lastError = :error WHERE id = :id")
    suspend fun markFailed(id: Long, error: String)

    /** Pending changes → Retry. */
    @Query("UPDATE pending_ops SET state = 'QUEUED', attempts = 0, nextAttemptAt = 0, lastError = NULL WHERE id = :id")
    suspend fun requeue(id: Long)

    @Query("SELECT COUNT(*) FROM pending_ops WHERE path LIKE 'calendar/%' OR path LIKE 'tasks%'")
    suspend fun calendarCount(): Int

    @Query("DELETE FROM pending_ops WHERE id = :id")
    suspend fun delete(id: Long)

    @Query("UPDATE pending_ops SET attempts = attempts + 1, lastError = :error WHERE id = :id")
    suspend fun failed(id: Long, error: String)
}

@Dao
interface LocalDraftDao {
    @Query("SELECT * FROM local_drafts WHERE id = :id")
    suspend fun get(id: String): LocalDraftEntity?

    @Query("SELECT * FROM local_drafts ORDER BY updatedAt ASC")
    suspend fun allNow(): List<LocalDraftEntity>

    @Query("SELECT * FROM local_drafts ORDER BY updatedAt DESC")
    fun all(): Flow<List<LocalDraftEntity>>

    @Query("SELECT * FROM local_drafts WHERE syncedVersion < version ORDER BY updatedAt ASC")
    suspend fun unsynced(): List<LocalDraftEntity>

    @Upsert
    suspend fun put(e: LocalDraftEntity)

    /** The server has [version] as draft [draftId]; a newer local edit stays unsynced. */
    @Query("UPDATE local_drafts SET draftId = :draftId, syncedVersion = MAX(syncedVersion, :version), error = NULL WHERE id = :id")
    suspend fun synced(id: String, draftId: Long, version: Long)

    @Query("UPDATE local_drafts SET error = :error WHERE id = :id")
    suspend fun setError(id: String, error: String?)

    @Query("UPDATE local_drafts SET closed = 1 WHERE id = :id")
    suspend fun close(id: String)

    @Query("DELETE FROM local_drafts WHERE id = :id")
    suspend fun delete(id: String)

    @Query("DELETE FROM local_drafts WHERE closed = 1 AND syncedVersion >= version")
    suspend fun dropSyncedClosed()

    @Query("DELETE FROM local_drafts")
    suspend fun clear()
}

/**
 * A "Send to self" share, from the moment the share sheet hands it over until the server has it.
 *
 * States: QUEUED (waiting for [io.github.murtazanasir.mailr.data.share.SelfSendWorker]), EDITING (paused
 * while the user adds a note), SENDING (claimed; the POST is in flight), HELD (the server holds
 * it for the undo window; `outboxId` can cancel it), CANCELLED, FAILED. Moves between states are
 * compare-and-set updates, so Undo and the worker never both win.
 */
@Entity(tableName = "self_sends")
data class SelfSendEntity(
    @PrimaryKey val id: String,
    /** Idempotency-Key for POST /send. A new key after an edit, the same one across retries. */
    val sendKey: String,
    val subject: String,
    val sharedSubject: String? = null,
    val sharedTitle: String? = null,
    val text: String = "",
    val note: String = "",
    /** JSON list of [io.github.murtazanasir.mailr.data.share.StagedFile]. */
    val filesJson: String = "[]",
    val state: String,
    val outboxId: Long? = null,
    val sendAt: Long? = null,
    val error: String? = null,
    val createdAt: Long,
    val attempts: Int = 0,
    /** The user's own labels picked on the card, comma-separated ids (version 8); "" for none. */
    @ColumnInfo(defaultValue = "") val labelIds: String = "",
)

@Dao
interface SelfSendDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(e: SelfSendEntity)

    @Query("SELECT * FROM self_sends WHERE id = :id")
    suspend fun get(id: String): SelfSendEntity?

    @Query("SELECT * FROM self_sends WHERE id = :id")
    fun watch(id: String): Flow<SelfSendEntity?>

    @Query("SELECT * FROM self_sends")
    suspend fun all(): List<SelfSendEntity>

    /** Compare-and-set; returns 1 when the row was in [from]. */
    @Query("UPDATE self_sends SET state = :to WHERE id = :id AND state = :from")
    suspend fun transition(id: String, from: String, to: String): Int

    /**
     * The Send tap: what the card holds (subject, note, labels) and QUEUED in one compare-and-set.
     * Returns 1 when the row was still waiting for the tap.
     */
    @Query("UPDATE self_sends SET subject = :subject, note = :note, labelIds = :labelIds, state = 'QUEUED' WHERE id = :id AND state = 'CONFIRM'")
    suspend fun confirm(id: String, subject: String, note: String, labelIds: String): Int

    @Query("UPDATE self_sends SET labelIds = :labelIds WHERE id = :id")
    suspend fun setLabels(id: String, labelIds: String)

    @Query("UPDATE self_sends SET state = 'HELD', outboxId = :outboxId, sendAt = :sendAt, error = NULL WHERE id = :id")
    suspend fun held(id: String, outboxId: Long, sendAt: Long?)

    @Query("UPDATE self_sends SET filesJson = :json WHERE id = :id")
    suspend fun setFiles(id: String, json: String)

    @Query("UPDATE self_sends SET subject = :subject WHERE id = :id")
    suspend fun setSubject(id: String, subject: String)

    @Query("UPDATE self_sends SET state = :state, error = :error, attempts = attempts + 1 WHERE id = :id")
    suspend fun setState(id: String, state: String, error: String?)

    @Query("UPDATE self_sends SET note = :note, sendKey = :key, state = 'QUEUED', outboxId = NULL, sendAt = NULL, error = NULL WHERE id = :id")
    suspend fun requeue(id: String, note: String, key: String)

    @Query("DELETE FROM self_sends WHERE id = :id")
    suspend fun delete(id: String)
}

/**
 * One event instance from the agenda (recurring series arrive expanded by the server).
 * `dirty` marks a local change the server has not confirmed yet; syncs leave such rows alone
 * while their change is queued. `deleted` hides a row whose deletion is pending (Undo window).
 */
@Entity(tableName = "cal_events", indices = [Index("startMs"), Index("uid")])
data class CalEventEntity(
    @PrimaryKey val key: String,
    val uid: String,
    val startMs: Long,
    val endMs: Long,
    val allDay: Boolean,
    val calendar: String?,
    val json: String,
    val dirty: Boolean = false,
    val deleted: Boolean = false,
    val localAt: Long = 0,
    // Version 6 (calendar build): the canonical occurrence, the mirror row id and the collection id.
    val occurrence: String? = null,
    val objectId: Long? = null,
    val calendarId: Long? = null,
)

@Entity(tableName = "cal_tasks", indices = [Index("parent")])
data class CalTaskEntity(
    @PrimaryKey val uid: String,
    val list: String?,
    val parent: String?,
    val completed: Boolean,
    val dueMs: Long?,
    val json: String,
    val dirty: Boolean = false,
    val deleted: Boolean = false,
    val localAt: Long = 0,
    // Version 6: mirror ids, the deferral start, the manual order and the version for If-Match.
    val objectId: Long? = null,
    val listId: Long? = null,
    val startMs: Long? = null,
    val sortOrder: Long? = null,
    val etag: String? = null,
)

/**
 * A calendar or task list, as JSON plus the columns the screens
 * filter on. `deviceHidden` is this phone's visibility override (null: follow `hidden`);
 * syncs never overwrite it.
 */
@Entity(tableName = "cal_collections")
data class CalCollectionEntity(
    @PrimaryKey val url: String,
    val calendarId: Long?,
    val name: String,
    val color: String?,
    val events: Boolean,
    val tasks: Boolean,
    val hidden: Boolean,
    val sortOrder: Int,
    val deviceHidden: Boolean?,
    val json: String,
)

/** A recurring (or single) event's master as the delta sent it, for editing "all events" offline. */
@Entity(tableName = "cal_event_masters")
data class CalMasterEntity(
    @PrimaryKey val uid: String,
    val objectId: Long?,
    val calendar: String?,
    val etag: String?,
    val json: String,
)

/** A task tag with the colour and order mailr keeps for it (GET /tasks/tags). */
@Entity(tableName = "cal_tags")
data class CalTagEntity(
    @PrimaryKey val name: String,
    val color: String?,
    val ord: Int,
)

/**
 * A write the server refused with 409 because the item changed elsewhere. `serverJson` is the
 * server copy from the conflict body; `method`, `path` and `body` are what this phone tried,
 * so "Keep mine" can send it again against the server's version. One per item (`key` =
 * "kind|uid"); a newer conflict replaces an older one.
 */
@Entity(tableName = "cal_conflicts")
data class CalConflictEntity(
    @PrimaryKey val key: String,
    val kind: String,
    val uid: String,
    val method: String,
    val path: String,
    val body: String,
    val serverJson: String?,
    val detail: String,
    val createdAt: Long,
)

/**
 * A reminder the app has planned, posted, snoozed or dismissed. This table is what makes the
 * alarms idempotent: every sync plans reminders afresh and diffs them against these rows
 * (see [io.github.murtazanasir.mailr.data.reminders.ReminderPlan]). Only the earliest PENDING row is armed
 * with AlarmManager; snoozes are rows too, so they survive reboots.
 */
@Entity(tableName = "reminders", indices = [Index("state"), Index("fireAt")])
data class ReminderEntity(
    @PrimaryKey val key: String,
    val kind: String,
    val itemUid: String,
    val occurrence: String?,
    val title: String,
    val detail: String,
    val fireAt: Long,
    val plannedAt: Long,
    val startAt: Long?,
    val endAt: Long?,
    val state: String,
    val snoozed: Boolean = false,
    val missed: Boolean = false,
    val updatedAt: Long,
)

@Dao
interface CalendarDao {
    @Query("SELECT * FROM cal_events WHERE deleted = 0 AND endMs > :from AND startMs < :to ORDER BY allDay DESC, startMs ASC")
    fun events(from: Long, to: Long): Flow<List<CalEventEntity>>

    @Query("SELECT * FROM cal_events WHERE deleted = 0 AND endMs > :from AND startMs < :to ORDER BY startMs ASC")
    suspend fun eventsNow(from: Long, to: Long): List<CalEventEntity>

    @Query("SELECT * FROM cal_events WHERE endMs > :from AND startMs < :to")
    suspend fun eventsInRange(from: Long, to: Long): List<CalEventEntity>

    @Query("SELECT * FROM cal_events WHERE uid = :uid")
    suspend fun eventsByUid(uid: String): List<CalEventEntity>

    @Query("SELECT * FROM cal_events WHERE `key` = :key")
    suspend fun event(key: String): CalEventEntity?

    @Upsert
    suspend fun putEvents(rows: List<CalEventEntity>)

    @Query("DELETE FROM cal_events WHERE `key` IN (:keys)")
    suspend fun dropEvents(keys: List<String>)

    @Query("UPDATE cal_events SET deleted = :deleted, dirty = 1, localAt = :at WHERE uid = :uid")
    suspend fun markEventsDeleted(uid: String, deleted: Boolean, at: Long)

    @Query("UPDATE cal_events SET deleted = :deleted, dirty = 1, localAt = :at WHERE `key` = :key")
    suspend fun markEventDeleted(key: String, deleted: Boolean, at: Long)

    @Query("UPDATE cal_events SET dirty = 0 WHERE uid = :uid")
    suspend fun cleanEvents(uid: String)

    @Transaction
    suspend fun replaceEvents(from: Long, to: Long, rows: List<CalEventEntity>, keepDirty: (CalEventEntity) -> Boolean) {
        val incoming = rows.associateBy { it.key }
        val old = eventsInRange(from, to)
        val kept = old.filter { it.dirty && keepDirty(it) }.map { it.key }.toSet()
        dropEvents(old.map { it.key }.filter { it !in kept })
        putEvents(incoming.values.filter { it.key !in kept })
    }

    @Query("SELECT * FROM cal_tasks WHERE deleted = 0")
    fun tasks(): Flow<List<CalTaskEntity>>

    @Query("SELECT * FROM cal_tasks WHERE deleted = 0")
    suspend fun tasksNow(): List<CalTaskEntity>

    @Query("SELECT * FROM cal_tasks WHERE uid = :uid")
    suspend fun task(uid: String): CalTaskEntity?

    @Query("SELECT * FROM cal_tasks")
    suspend fun allTasks(): List<CalTaskEntity>

    @Upsert
    suspend fun putTasks(rows: List<CalTaskEntity>)

    @Query("DELETE FROM cal_tasks WHERE uid IN (:uids)")
    suspend fun dropTasks(uids: List<String>)

    @Transaction
    suspend fun replaceTasks(rows: List<CalTaskEntity>, completedToo: Boolean, keepDirty: (CalTaskEntity) -> Boolean) {
        val incoming = rows.associateBy { it.uid }
        val old = allTasks().filter { completedToo || !it.completed }
        val kept = old.filter { it.dirty && keepDirty(it) }.map { it.uid }.toSet()
        dropTasks(old.map { it.uid }.filter { it !in kept && it !in incoming })
        putTasks(incoming.values.filter { it.uid !in kept })
    }

    @Query("DELETE FROM cal_events")
    suspend fun clearEvents()

    @Query("DELETE FROM cal_tasks")
    suspend fun clearTasks()

    @Query("DELETE FROM cal_events WHERE uid IN (:uids)")
    suspend fun dropEventsOf(uids: List<String>)

    @Query("SELECT DISTINCT uid FROM cal_events")
    suspend fun eventUids(): List<String>

    @Query("SELECT * FROM cal_events WHERE calendar = :url")
    suspend fun eventsInCollection(url: String): List<CalEventEntity>

    @Query("SELECT * FROM cal_tasks WHERE list = :url")
    suspend fun tasksInList(url: String): List<CalTaskEntity>

    @Query("SELECT * FROM cal_tasks WHERE parent = :uid")
    suspend fun children(uid: String): List<CalTaskEntity>

    // ---- collections ----

    @Query("SELECT * FROM cal_collections ORDER BY sortOrder ASC, name ASC")
    fun collections(): Flow<List<CalCollectionEntity>>

    @Query("SELECT * FROM cal_collections ORDER BY sortOrder ASC, name ASC")
    suspend fun collectionsNow(): List<CalCollectionEntity>

    @Query("SELECT * FROM cal_collections WHERE url = :url")
    suspend fun collection(url: String): CalCollectionEntity?

    @Query("SELECT * FROM cal_collections WHERE calendarId = :id")
    suspend fun collectionById(id: Long): CalCollectionEntity?

    @Upsert
    suspend fun putCollections(rows: List<CalCollectionEntity>)

    @Query("DELETE FROM cal_collections WHERE url IN (:urls)")
    suspend fun dropCollections(urls: List<String>)

    @Query("UPDATE cal_collections SET deviceHidden = :hidden WHERE url = :url")
    suspend fun setDeviceHidden(url: String, hidden: Boolean?)

    @Query("DELETE FROM cal_collections")
    suspend fun clearCollections()

    // ---- masters ----

    @Query("SELECT * FROM cal_event_masters WHERE uid = :uid")
    suspend fun master(uid: String): CalMasterEntity?

    @Upsert
    suspend fun putMasters(rows: List<CalMasterEntity>)

    @Query("DELETE FROM cal_event_masters WHERE uid IN (:uids)")
    suspend fun dropMasters(uids: List<String>)

    @Query("SELECT uid FROM cal_event_masters")
    suspend fun masterUids(): List<String>

    @Query("DELETE FROM cal_event_masters WHERE calendar = :url")
    suspend fun dropMastersIn(url: String)

    @Query("DELETE FROM cal_event_masters")
    suspend fun clearMasters()

    // ---- tags ----

    @Query("SELECT * FROM cal_tags ORDER BY ord ASC, name ASC")
    fun tags(): Flow<List<CalTagEntity>>

    @Query("DELETE FROM cal_tags")
    suspend fun clearTags()

    @Upsert
    suspend fun putTags(rows: List<CalTagEntity>)

    @Transaction
    suspend fun replaceTags(rows: List<CalTagEntity>) {
        clearTags()
        putTags(rows)
    }

    // ---- conflicts ----

    @Query("SELECT * FROM cal_conflicts ORDER BY createdAt ASC")
    fun conflicts(): Flow<List<CalConflictEntity>>

    @Query("SELECT * FROM cal_conflicts WHERE `key` = :key")
    suspend fun conflict(key: String): CalConflictEntity?

    @Upsert
    suspend fun putConflict(row: CalConflictEntity)

    @Query("DELETE FROM cal_conflicts WHERE `key` = :key")
    suspend fun dropConflict(key: String)

    @Query("DELETE FROM cal_conflicts")
    suspend fun clearConflicts()
}

@Dao
interface ReminderDao {
    @Query("SELECT * FROM reminders")
    suspend fun all(): List<ReminderEntity>

    @Query("SELECT * FROM reminders WHERE `key` = :key")
    suspend fun get(key: String): ReminderEntity?

    @Query("SELECT * FROM reminders WHERE state = 'SHOWN' AND kind = :kind ORDER BY fireAt ASC")
    suspend fun shown(kind: String): List<ReminderEntity>

    @Query("SELECT * FROM reminders WHERE state = 'SHOWN' ORDER BY fireAt ASC")
    suspend fun allShown(): List<ReminderEntity>

    /** The reminders showing now (fired or missed), for the "Waiting for you" screen and its bell. */
    @Query("SELECT * FROM reminders WHERE state = 'SHOWN' ORDER BY fireAt ASC")
    fun shownFlow(): Flow<List<ReminderEntity>>

    @Query("SELECT * FROM reminders WHERE state = 'PENDING' AND fireAt <= :until ORDER BY fireAt ASC")
    suspend fun due(until: Long): List<ReminderEntity>

    @Query("SELECT MIN(fireAt) FROM reminders WHERE state = 'PENDING'")
    suspend fun nextFire(): Long?

    @Query("SELECT * FROM reminders WHERE state = 'PENDING' ORDER BY fireAt ASC LIMIT :limit")
    fun upcoming(limit: Int): Flow<List<ReminderEntity>>

    @Upsert
    suspend fun put(rows: List<ReminderEntity>)

    @Query("DELETE FROM reminders WHERE `key` IN (:keys)")
    suspend fun delete(keys: List<String>)

    @Query("UPDATE reminders SET state = :state, updatedAt = :at WHERE `key` = :key")
    suspend fun setState(key: String, state: String, at: Long)

    @Query("UPDATE reminders SET state = :to, updatedAt = :at WHERE state = :from AND kind = :kind")
    suspend fun setStateForKind(kind: String, from: String, to: String, at: Long)

    @Query("UPDATE reminders SET state = 'DONE', updatedAt = :at WHERE itemUid = :uid AND kind = :kind AND state IN ('PENDING', 'SHOWN')")
    suspend fun doneForItem(kind: String, uid: String, at: Long)

    @Query("SELECT * FROM reminders WHERE itemUid = :uid AND kind = :kind AND state = 'SHOWN'")
    suspend fun shownForItem(kind: String, uid: String): List<ReminderEntity>

    @Query("DELETE FROM reminders")
    suspend fun clear()
}

/** Every migration, in order. A new schema version adds its MIGRATION_n_n+1 here. */
val MIGRATIONS: Array<Migration> = arrayOf(MIGRATION_5_6, MIGRATION_6_7, MIGRATION_7_8, MIGRATION_8_9)

/** Versions from before the first migration: rebuilt from the server (there was no queue then). */
val DESTRUCTIVE_FROM: IntArray = intArrayOf(1, 2, 3, 4)

@Database(
    entities = [ListItemEntity::class, RemoteKeyEntity::class, ConversationEntity::class,
        BodyEntity::class, PendingOpEntity::class, KvEntity::class, SelfSendEntity::class, LocalDraftEntity::class,
        CalEventEntity::class, CalTaskEntity::class, ReminderEntity::class,
        CalCollectionEntity::class, CalMasterEntity::class, CalTagEntity::class, CalConflictEntity::class,
        // Notes, checklists and tables (the shared module's NOTES_ENTITIES).
        io.github.murtazanasir.mailr.notes.data.NoteEntity::class, io.github.murtazanasir.mailr.notes.data.ItemEntity::class,
        io.github.murtazanasir.mailr.notes.data.SectionEntity::class, io.github.murtazanasir.mailr.notes.data.LabelEntity::class,
        io.github.murtazanasir.mailr.notes.data.TableEntity::class, io.github.murtazanasir.mailr.notes.data.RowEntity::class,
        io.github.murtazanasir.mailr.notes.data.OpEntity::class, io.github.murtazanasir.mailr.notes.data.NotesKv::class],
    // 4: conversation/message ids became opaque public ids; the cache is rebuilt from the server.
    // 5: rebuild the cache with public ids (any internal ids cached by a v4 build are dropped)
    // 6: calendar build (new calendar/task columns and tables; MIGRATION_5_6 keeps the data)
    // 7: pending_ops state and nextAttemptAt, local_drafts (MIGRATION_6_7 keeps the data)
    // 8: self_sends.labelIds, the labels picked on the Send to self card (MIGRATION_7_8)
    version = 9, // 9: the notes_* tables of the Notes module and its replay queue (MIGRATION_8_9)
    exportSchema = true,
    autoMigrations = [AutoMigration(from = 1, to = 2), AutoMigration(from = 2, to = 3)],
)
abstract class MailrDb : RoomDatabase(), io.github.murtazanasir.mailr.notes.data.NotesDatabase {
    abstract override fun notesDao(): io.github.murtazanasir.mailr.notes.data.NotesDao
    override val room: RoomDatabase get() = this
    abstract fun lists(): ListDao
    abstract fun cache(): CacheDao
    abstract fun pending(): PendingDao
    abstract fun selfSends(): SelfSendDao
    abstract fun localDrafts(): LocalDraftDao
    abstract fun calendar(): CalendarDao
    abstract fun reminders(): ReminderDao

    companion object {
        fun create(context: Context, inMemory: Boolean = false): MailrDb {
            val b = if (inMemory) Room.inMemoryDatabaseBuilder(context, MailrDb::class.java)
            else Room.databaseBuilder(context, MailrDb::class.java, "mailr.db")
            // Mostly a cache of server state, but pending_ops, self_sends and local_drafts hold
            // the user's unsent work. Every upgrade from version 5 on has a migration that keeps
            // them (MIGRATIONS); only databases older than any shipped migration are rebuilt.
            // A schema bump without a migration fails loudly (MigrationChainTest) instead of
            // wiping the offline queue.
            return b.addMigrations(*MIGRATIONS)
                .fallbackToDestructiveMigrationFrom(*DESTRUCTIVE_FROM)
                .fallbackToDestructiveMigrationOnDowngrade()
                .build()
        }
    }
}
