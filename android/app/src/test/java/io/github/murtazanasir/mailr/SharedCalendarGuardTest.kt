// ACT-SURFACES: android.calendar android.tasks
package io.github.murtazanasir.mailr

import android.app.Application
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.ui.calendar.sharedLabel
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Group calendars and calendars another person shares (server `shared`): the app
 * offers no Rename or Delete for them (calendar.rename, calendar.delete, list.rename, list.delete),
 * and the repository refuses both before anything reaches the server. Colour stays the user's own.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class SharedCalendarGuardTest {
    private val h = CalendarHarness()

    @Before fun setUp() { h.start() }
    @After fun tearDown() = h.stop()

    private fun coll(json: String): CalendarCollection = MailrJson.decodeFromString(CalendarCollection.serializer(), json)

    private val group = """{"url":"c1","name":"Family","calendar_id":11,"events":true,"read_only":false,
        "shared":{"kind":"group","group":"family","access":"write","editor":false,"group_calendar_id":4}}"""
    private val byUser = """{"url":"c2","name":"Rosa's plans","calendar_id":12,"events":true,"read_only":true,
        "shared":{"kind":"user","owner":"Rosa","owner_id":7,"level":"view","access":"read","can_share":false}}"""
    private val own = """{"url":"c3","name":"Calendar","calendar_id":13,"events":true,"read_only":false,"sharing":{"people":2,"others":0}}"""

    @Test fun shared_calendars_offer_no_rename_or_delete() {
        val g = coll(group)
        val u = coll(byUser)
        val o = coll(own)
        // ACT calendar.rename, ACT calendar.delete, ACT list.rename, ACT list.delete: only own calendars.
        assertEquals("group", g.shared?.kind)
        assertEquals("Rosa", u.shared?.owner)
        assertFalse(g.canRenameOrDelete)
        assertFalse(u.canRenameOrDelete)
        assertTrue(o.canRenameOrDelete)
        assertFalse(o.copy(readOnly = true).canRenameOrDelete)
        assertEquals("Only an administrator can rename or delete a group calendar. You can hide it.", g.ownerNote)
        assertEquals("Only Rosa can rename or delete it. You can hide it.", u.ownerNote)
        assertNull(o.ownerNote)
        assertEquals("Shared with the family group", sharedLabel(g))
        assertEquals("Shared by Rosa", sharedLabel(u))
        assertNull(sharedLabel(o))
    }

    /** Server calendar/sharing.py: free/busy only (Stalwart 1.x), and access a group of the user's gives. */
    @Test fun free_busy_only_and_access_through_a_group_are_named() {
        val fb = coll("""{"url":"c4","name":"Rosa's rota","calendar_id":14,"events":true,"read_only":true,
            "shared":{"kind":"user","owner":"Rosa","owner_id":7,"level":"freebusy","access":"freebusy","can_share":false}}""")
        val viaGroup = coll("""{"url":"c5","name":"Rosa's plans","calendar_id":15,"events":true,"read_only":false,
            "shared":{"kind":"user","owner":"Rosa","owner_id":7,"level":"edit","access":"write","can_share":false,
            "own_level":"view","via":["Family"]}}""")
        val share = coll("""{"url":"c6","name":"Board","calendar_id":16,"events":true,"read_only":false,
            "shared":{"kind":"user","owner":"Rosa","level":"share","access":"write","can_share":true,"via":["Family","Team"]}}""")
        assertEquals("Shared by Rosa · free/busy only", sharedLabel(fb))
        assertEquals("Shared by Rosa · can edit (through Family)", sharedLabel(viaGroup))
        assertEquals("Shared by Rosa · can edit and share (through Family, Team)", sharedLabel(share))
        assertFalse(fb.canRenameOrDelete)
        assertEquals(listOf("Family"), viaGroup.shared?.via)
    }

    /** Server calendar/groups.py: a group calendar shared with the group as free/busy only (Stalwart 1.x). */
    @Test fun a_free_busy_only_group_calendar_is_named() {
        val fb = coll("""{"url":"c7","name":"Rota","calendar_id":17,"events":true,"read_only":true,
            "shared":{"kind":"group","group":"family","access":"freebusy","editor":false,"group_calendar_id":5}}""")
        val editor = coll("""{"url":"c8","name":"Rota","calendar_id":18,"events":true,"read_only":false,
            "shared":{"kind":"group","group":"family","access":"write","editor":true,"group_calendar_id":5}}""")
        assertEquals("Shared with the family group · free/busy only", sharedLabel(fb))
        assertEquals("Shared with the family group", sharedLabel(editor))
        assertFalse(fb.canRenameOrDelete)
        assertEquals("Only an administrator can rename or delete a group calendar. You can hide it.", fb.ownerNote)
    }

    @Test fun the_repository_refuses_rename_and_delete_of_a_shared_calendar() = runBlocking {
        val g = coll(group)
        val before = h.requests.size
        try {
            h.g.calendar.deleteCollection(g, g.name)
            fail("deleted a group calendar")
        } catch (e: IllegalStateException) {
            assertEquals(g.ownerNote, e.message)
        }
        try {
            h.g.calendar.updateCollection(g, name = "Ours")
            fail("renamed a group calendar")
        } catch (e: IllegalStateException) {
            assertEquals(g.ownerNote, e.message)
        }
        assertEquals("nothing reached the server", before, h.requests.size)
    }
}
