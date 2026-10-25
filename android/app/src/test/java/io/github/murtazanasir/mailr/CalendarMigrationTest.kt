package io.github.murtazanasir.mailr

import android.app.Application
import android.database.sqlite.SQLiteDatabase
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.db.CalCollectionEntity
import io.github.murtazanasir.mailr.data.db.CalConflictEntity
import io.github.murtazanasir.mailr.data.db.MIGRATIONS
import io.github.murtazanasir.mailr.data.db.MailrDb
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

/**
 * Room 5 → 6 (calendar build): a database written by the version-5 app keeps its offline
 * queue, reminder states and calendar cache, and the new columns and tables work afterwards.
 * The version-5 database is built from the exported schema (schemas/…/5.json).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class CalendarMigrationTest {
    private val app: Application = ApplicationProvider.getApplicationContext()

    private fun createV5(name: String) {
        val file = app.getDatabasePath(name).also { it.parentFile?.mkdirs(); it.delete() }
        val schema = Json.parseToJsonElement(File("schemas/io.github.murtazanasir.mailr.data.db.MailrDb/5.json").readText())
            .jsonObject["database"]!!.jsonObject
        val db = SQLiteDatabase.openOrCreateDatabase(file, null)
        for (e in schema["entities"]!!.jsonArray) {
            val t = e.jsonObject
            val table = t["tableName"]!!.jsonPrimitive.content
            db.execSQL(t["createSql"]!!.jsonPrimitive.content.replace("\${TABLE_NAME}", table))
            t["indices"]?.jsonArray?.forEach { i -> db.execSQL(i.jsonObject["createSql"]!!.jsonPrimitive.content.replace("\${TABLE_NAME}", table)) }
        }
        schema["setupQueries"]!!.jsonArray.forEach { db.execSQL(it.jsonPrimitive.content) }
        db.execSQL("INSERT INTO pending_ops (idempotencyKey, method, path, body, description, createdAt, attempts) " +
            "VALUES ('k1', 'PATCH', 'tasks/t1', '{\"completed\":true}', 'Complete t1', 1, 0)")
        db.execSQL("INSERT INTO cal_tasks (uid, list, parent, completed, dueMs, json, dirty, deleted, localAt) " +
            "VALUES ('t1', 'https://dav/l/', NULL, 0, 1000, '{\"uid\":\"t1\",\"summary\":\"Pay rent\"}', 1, 0, 5)")
        db.execSQL("INSERT INTO cal_events (`key`, uid, startMs, endMs, allDay, calendar, json, dirty, deleted, localAt) " +
            "VALUES ('e1|2026-10-02T11:00:00-04:00', 'e1', 10, 20, 0, 'https://dav/c/', '{\"uid\":\"e1\",\"start\":\"2026-10-02T11:00:00-04:00\"}', 0, 0, 0)")
        db.execSQL("INSERT INTO reminders (`key`, kind, itemUid, occurrence, title, detail, fireAt, plannedAt, startAt, endAt, state, snoozed, missed, updatedAt) " +
            "VALUES ('e|e1|2026-10-02T11:00:00-04:00|10', 'EVENT', 'e1', '2026-10-02T11:00:00-04:00', 'Budget', '', 1, 1, 10, 20, 'DISMISSED', 0, 0, 1)")
        db.execSQL("INSERT INTO kv (`key`, value) VALUES ('changes_cursor', '77')")
        db.version = 5
        db.close()
    }

    @Test fun version5DataSurvivesAndNewTablesWork() = runBlocking {
        createV5("migrate-test.db")
        // Only the explicit migration: a failed or missing one throws instead of rebuilding.
        val db = Room.databaseBuilder(app, MailrDb::class.java, "migrate-test.db").addMigrations(*MIGRATIONS).build()
        try {
            val op = db.pending().next(10).single()
            assertEquals("tasks/t1", op.path)
            assertEquals("k1", op.idempotencyKey)
            val task = db.calendar().task("t1")!!
            assertEquals(true, task.dirty)
            assertNull(task.etag)
            assertNull(task.sortOrder)
            assertEquals("e1", db.calendar().eventsByUid("e1").single().uid)
            assertEquals("DISMISSED", db.reminders().get("e|e1|2026-10-02T11:00:00-04:00|10")!!.state)
            assertEquals("77", db.cache().get("changes_cursor"))

            // The new columns and tables.
            db.calendar().putTasks(listOf(task.copy(etag = "\"v2\"", sortOrder = 2048, listId = 4)))
            assertEquals("\"v2\"", db.calendar().task("t1")!!.etag)
            db.calendar().putCollections(listOf(CalCollectionEntity("https://dav/l/", 4, "Home", "#1a73e8", false, true, false, 0,
                deviceHidden = true, json = "{\"url\":\"https://dav/l/\"}")))
            assertEquals(true, db.calendar().collection("https://dav/l/")!!.deviceHidden)
            db.calendar().putConflict(CalConflictEntity("task|t1", "task", "t1", "PATCH", "tasks/t1", "{}", null, "changed", 1))
            assertEquals(1, db.calendar().conflicts().first().size)
        } finally {
            db.close()
            app.deleteDatabase("migrate-test.db")
        }
    }
}
