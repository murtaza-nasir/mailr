package io.github.murtazanasir.mailr

import android.app.Application
import android.database.sqlite.SQLiteDatabase
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.db.MailrDb
import io.github.murtazanasir.mailr.data.db.PendingState
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

/**
 * Upgrading the database from version 6 (0.5.2) to 7 keeps what the user did and the
 * server has not seen: the offline queue and pending self-sends. The version-6 file is built
 * from the exported schema (app/schemas), then opened through [MailrDb.create], with its
 * destructive fallback, exactly as the app opens it.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class DbMigrationTest {
    private val app = ApplicationProvider.getApplicationContext<Application>()

    private fun schema(version: Int): JsonObject {
        val f = listOf("schemas", "app/schemas").map { File(it, "io.github.murtazanasir.mailr.data.db.MailrDb/$version.json") }.first { it.exists() }
        return kotlinx.serialization.json.Json.parseToJsonElement(f.readText()).jsonObject["database"]!!.jsonObject
    }

    private fun createV6(file: File) = createAt(file, 6)

    private fun createAt(file: File, version: Int) {
        file.parentFile!!.mkdirs()
        val db = SQLiteDatabase.openOrCreateDatabase(file, null)
        val s = schema(version)
        for (e in s["entities"]!!.jsonArray.map { it.jsonObject }) {
            val table = e["tableName"]!!.jsonPrimitive.content
            db.execSQL(e["createSql"]!!.jsonPrimitive.content.replace("\${TABLE_NAME}", table))
            e["indices"]?.jsonArray?.forEach { i ->
                db.execSQL(i.jsonObject["createSql"]!!.jsonPrimitive.content.replace("\${TABLE_NAME}", table))
            }
        }
        s["setupQueries"]!!.jsonArray.forEach { db.execSQL(it.jsonPrimitive.content) }
        db.execSQL("INSERT INTO pending_ops (idempotencyKey, method, path, body, description, createdAt, attempts, lastError) " +
            "VALUES ('k-1', 'POST', 'conversations/actions', '{\"ids\":[3070],\"action\":\"archive\"}', 'archive 1', 1000, 2, 'offline')")
        db.execSQL("INSERT INTO self_sends (id, sendKey, subject, text, note, filesJson, state, createdAt, attempts) " +
            "VALUES ('s-1', 'key-1', 'A link', 'https://example.com', '', '[]', 'QUEUED', 1000, 0)")
        db.version = version
        db.close()
    }

    @Test fun version6To7KeepsTheQueueAndSelfSends() = runBlocking {
        val file = app.getDatabasePath("mailr.db")
        file.delete()
        createV6(file)
        val db = MailrDb.create(app)
        val op = db.pending().next(10).single()
        assertEquals("k-1", op.idempotencyKey)
        assertEquals(2, op.attempts)
        assertEquals(PendingState.QUEUED, op.state)
        assertEquals(0L, op.nextAttemptAt)
        assertNotNull(db.selfSends().get("s-1"))
        // The new table is there and usable.
        assertTrue(db.localDrafts().allNow().isEmpty())
        db.close()
        assertEquals(9, SQLiteDatabase.openDatabase(file.path, null, SQLiteDatabase.OPEN_READONLY).use { it.version })
    }

    /** 0.5.9: version 7 → 8 adds the labels picked on the Send to self card; a queued share stays, with none. */
    @Test fun version7To8KeepsSelfSendsAndAddsTheirLabels() = runBlocking {
        val file = app.getDatabasePath("mailr.db")
        file.delete()
        createAt(file, 7)
        val db = MailrDb.create(app)
        val row = db.selfSends().get("s-1")!!
        assertEquals("QUEUED", row.state)
        assertEquals("", row.labelIds)
        assertEquals(1, db.pending().next(10).size)
        db.close()
        assertEquals(9, SQLiteDatabase.openDatabase(file.path, null, SQLiteDatabase.OPEN_READONLY).use { it.version })
    }

    /**
     * Version 8 → 9 adds the Notes module's tables (notes,
     * items, sections, labels, tables, rows, its replay queue and small values). The mail queue and
     * self-sends stay, and Room's own check of the new tables passes (it fails the open otherwise).
     */
    @Test fun version8To9AddsTheNotesTablesAndKeepsTheQueue() = runBlocking {
        val file = app.getDatabasePath("mailr.db")
        file.delete()
        createAt(file, 8)
        val db = MailrDb.create(app)
        assertEquals(1, db.pending().next(10).size)
        assertNotNull(db.selfSends().get("s-1"))
        val notes = db.notesDao()
        notes.putNotes(listOf(io.github.murtazanasir.mailr.notes.data.NoteEntity("n1", "{\"id\":\"n1\"}")))
        val op = notes.addOp(io.github.murtazanasir.mailr.notes.data.OpEntity(key = "k", method = "PATCH", path = "notes/n1/items/i1", body = "{}",
            kind = "item", target = "i1", parent = "n1", label = "Checked", createdAt = 1))
        assertEquals(io.github.murtazanasir.mailr.notes.data.OpState.QUEUED, notes.op(op)!!.state)
        assertEquals("n1", notes.noteNow("n1")!!.id)
        db.close()
        assertEquals(9, SQLiteDatabase.openDatabase(file.path, null, SQLiteDatabase.OPEN_READONLY).use { it.version })
    }
}
