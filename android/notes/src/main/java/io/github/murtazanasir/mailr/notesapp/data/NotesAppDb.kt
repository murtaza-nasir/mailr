package io.github.murtazanasir.mailr.notesapp.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import io.github.murtazanasir.mailr.notes.data.ItemEntity
import io.github.murtazanasir.mailr.notes.data.LabelEntity
import io.github.murtazanasir.mailr.notes.data.NoteEntity
import io.github.murtazanasir.mailr.notes.data.NotesDao
import io.github.murtazanasir.mailr.notes.data.NotesDatabase
import io.github.murtazanasir.mailr.notes.data.NotesKv
import io.github.murtazanasir.mailr.notes.data.OpEntity
import io.github.murtazanasir.mailr.notes.data.RowEntity
import io.github.murtazanasir.mailr.notes.data.SectionEntity
import io.github.murtazanasir.mailr.notes.data.TableEntity

/**
 * The Notes app's own database (one file per app, the notes
 * tables inside it): the shared module's entities (NOTES_ENTITIES) and nothing else. Version 1; a
 * later version adds a migration, as the mailr app's MailrDb does, so the replay queue survives.
 */
@Database(
    entities = [NoteEntity::class, ItemEntity::class, SectionEntity::class, LabelEntity::class,
        TableEntity::class, RowEntity::class, OpEntity::class, NotesKv::class],
    version = 1,
    exportSchema = true,
)
abstract class NotesAppDb : RoomDatabase(), NotesDatabase {
    abstract override fun notesDao(): NotesDao
    override val room: RoomDatabase get() = this

    companion object {
        fun create(context: Context, inMemory: Boolean = false): NotesAppDb =
            (if (inMemory) Room.inMemoryDatabaseBuilder(context, NotesAppDb::class.java)
            else Room.databaseBuilder(context, NotesAppDb::class.java, "notes.db")).build()
    }
}
