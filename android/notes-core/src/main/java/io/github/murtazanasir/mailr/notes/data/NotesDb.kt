package io.github.murtazanasir.mailr.notes.data

import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.RoomDatabase
import androidx.room.Upsert
import kotlinx.coroutines.flow.Flow

/*
 * The phone's copy of notes, checklists and tables, for offline use: every note, item, section and table the
 * user can see, kept as the server's JSON, plus the item-level replay queue. The entities live
 * here; the host's Room database lists them (NOTES_ENTITIES) and implements [NotesDatabase], so the
 * mailr app keeps one database file and the Notes app has a database of its own with the
 * same tables.
 */

/** A note (its JSON without items and sections). [deleted]: in Trash. */
@Entity(tableName = "notes_notes")
data class NoteEntity(@PrimaryKey val id: String, val json: String, val deleted: Boolean = false)

/** A checklist item. [removed]: deleted on the phone, the delete still queued. [conflict]: a 409 body waiting for the user's choice. */
@Entity(tableName = "notes_items", indices = [Index("noteId")])
data class ItemEntity(@PrimaryKey val id: String, val noteId: String, val json: String,
                      val removed: Boolean = false, val conflict: String? = null)

@Entity(tableName = "notes_sections", indices = [Index("noteId")])
data class SectionEntity(@PrimaryKey val id: String, val noteId: String, val json: String, val removed: Boolean = false)

@Entity(tableName = "notes_labels")
data class LabelEntity(@PrimaryKey val name: String, val json: String)

/** A table with its columns and views. [deleted]: in Trash. */
@Entity(tableName = "notes_tables")
data class TableEntity(@PrimaryKey val id: String, val json: String, val deleted: Boolean = false)

@Entity(tableName = "notes_rows", indices = [Index("tableId")])
data class RowEntity(@PrimaryKey val id: String, val tableId: String, val json: String,
                     val removed: Boolean = false, val conflict: String? = null)

/**
 * One write waiting for the server (the item-level replay queue). Sent in order, each with
 * its own [key] as `Idempotency-Key`, so a write sent twice lands once. [target] is the item, row,
 * section or note the write touches; [parent] its note or table. [state]: QUEUED, FAILED (the
 * server refused it; Retry or Discard) or CONFLICT (a 409 the user resolves on the row).
 * [creates]: a temporary id ("local-…") this write makes real; later writes naming it are
 * rewritten to the server's id when it answers.
 */
@Entity(tableName = "notes_ops", indices = [Index("parent")])
data class OpEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val key: String,
    val method: String,
    val path: String,
    val body: String,
    val kind: String,
    val target: String,
    val parent: String,
    val label: String,
    val createdAt: Long,
    val attempts: Int = 0,
    @ColumnInfo(defaultValue = "QUEUED") val state: String = OpState.QUEUED,
    val error: String? = null,
    val creates: String? = null,
)

object OpState {
    const val QUEUED = "QUEUED"
    const val FAILED = "FAILED"
    const val CONFLICT = "CONFLICT"
}

@Entity(tableName = "notes_kv")
data class NotesKv(@PrimaryKey val key: String, val value: String)

/** The entities a host database lists for the Notes module. */
val NOTES_ENTITIES = arrayOf(NoteEntity::class, ItemEntity::class, SectionEntity::class, LabelEntity::class,
    TableEntity::class, RowEntity::class, OpEntity::class, NotesKv::class)

/** What a host database gives the Notes module. */
interface NotesDatabase {
    fun notesDao(): NotesDao
    /** The Room database itself, for transactions. */
    val room: RoomDatabase
}

@Dao
interface NotesDao {
    // ---- notes
    @Query("SELECT * FROM notes_notes") fun notes(): Flow<List<NoteEntity>>
    @Query("SELECT * FROM notes_notes") suspend fun allNotes(): List<NoteEntity>
    @Query("SELECT * FROM notes_notes WHERE id = :id") fun note(id: String): Flow<NoteEntity?>
    @Query("SELECT * FROM notes_notes WHERE id = :id") suspend fun noteNow(id: String): NoteEntity?
    @Upsert suspend fun putNotes(rows: List<NoteEntity>)
    @Query("DELETE FROM notes_notes WHERE id = :id") suspend fun deleteNote(id: String)

    // ---- items and sections
    @Query("SELECT * FROM notes_items WHERE noteId = :noteId") fun items(noteId: String): Flow<List<ItemEntity>>
    @Query("SELECT * FROM notes_items WHERE noteId = :noteId") suspend fun itemsNow(noteId: String): List<ItemEntity>
    @Query("SELECT * FROM notes_items") fun allItems(): Flow<List<ItemEntity>>
    @Query("SELECT * FROM notes_items") suspend fun allItemsNow(): List<ItemEntity>
    @Query("SELECT * FROM notes_items WHERE id = :id") suspend fun item(id: String): ItemEntity?
    @Upsert suspend fun putItems(rows: List<ItemEntity>)
    @Query("DELETE FROM notes_items WHERE id = :id") suspend fun deleteItem(id: String)
    @Query("DELETE FROM notes_items WHERE noteId = :noteId") suspend fun deleteItemsOf(noteId: String)
    @Query("SELECT * FROM notes_sections WHERE noteId = :noteId") fun sections(noteId: String): Flow<List<SectionEntity>>
    @Query("SELECT * FROM notes_sections WHERE noteId = :noteId") suspend fun sectionsNow(noteId: String): List<SectionEntity>
    @Query("SELECT * FROM notes_sections") fun allSections(): Flow<List<SectionEntity>>
    @Query("SELECT * FROM notes_sections") suspend fun allSectionsNow(): List<SectionEntity>
    @Upsert suspend fun putSections(rows: List<SectionEntity>)
    @Query("DELETE FROM notes_sections WHERE noteId = :noteId") suspend fun deleteSectionsOf(noteId: String)
    @Query("DELETE FROM notes_sections WHERE id = :id") suspend fun deleteSection(id: String)

    // ---- labels
    @Query("SELECT * FROM notes_labels") fun labels(): Flow<List<LabelEntity>>
    @Query("SELECT * FROM notes_labels") suspend fun labelsNow(): List<LabelEntity>
    @Upsert suspend fun putLabels(rows: List<LabelEntity>)
    @Query("DELETE FROM notes_labels") suspend fun clearLabels()
    @Query("DELETE FROM notes_labels WHERE name = :name") suspend fun deleteLabel(name: String)

    // ---- tables and rows
    @Query("SELECT * FROM notes_tables") fun tables(): Flow<List<TableEntity>>
    @Query("SELECT * FROM notes_tables") suspend fun allTables(): List<TableEntity>
    @Query("SELECT * FROM notes_tables WHERE id = :id") fun table(id: String): Flow<TableEntity?>
    @Query("SELECT * FROM notes_tables WHERE id = :id") suspend fun tableNow(id: String): TableEntity?
    @Upsert suspend fun putTables(rows: List<TableEntity>)
    @Query("DELETE FROM notes_tables WHERE id = :id") suspend fun deleteTable(id: String)
    @Query("SELECT * FROM notes_rows WHERE tableId = :tableId") fun rows(tableId: String): Flow<List<RowEntity>>
    @Query("SELECT * FROM notes_rows WHERE tableId = :tableId") suspend fun rowsNow(tableId: String): List<RowEntity>
    @Query("SELECT * FROM notes_rows WHERE id = :id") suspend fun row(id: String): RowEntity?
    @Query("SELECT * FROM notes_rows WHERE id = :id") fun rowFlow(id: String): Flow<RowEntity?>
    @Upsert suspend fun putRows(rows: List<RowEntity>)
    @Query("DELETE FROM notes_rows WHERE id = :id") suspend fun deleteRow(id: String)
    @Query("DELETE FROM notes_rows WHERE tableId = :tableId") suspend fun deleteRowsOf(tableId: String)

    // ---- the replay queue
    @Query("SELECT * FROM notes_ops ORDER BY id") fun ops(): Flow<List<OpEntity>>
    @Query("SELECT * FROM notes_ops ORDER BY id") suspend fun opsNow(): List<OpEntity>
    @Query("SELECT * FROM notes_ops WHERE id = :id") suspend fun op(id: Long): OpEntity?
    @androidx.room.Insert suspend fun addOp(op: OpEntity): Long
    @androidx.room.Update suspend fun updateOp(op: OpEntity)
    @Query("DELETE FROM notes_ops WHERE id = :id") suspend fun deleteOp(id: Long)
    @Query("SELECT COUNT(*) FROM notes_ops WHERE parent = :parent OR target = :parent") suspend fun opsFor(parent: String): Int

    // ---- small values (the sync cursors, a table view's row order)
    @Query("SELECT value FROM notes_kv WHERE `key` = :key") suspend fun kv(key: String): String?
    @Query("SELECT value FROM notes_kv WHERE `key` = :key") fun kvFlow(key: String): Flow<String?>
    @Upsert suspend fun putKv(row: NotesKv)
    @Query("DELETE FROM notes_kv WHERE `key` = :key") suspend fun deleteKv(key: String)
    @Query("DELETE FROM notes_kv WHERE `key` LIKE :prefix || '%'") suspend fun deleteKvPrefix(prefix: String)
}
