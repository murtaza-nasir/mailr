// ACT-SURFACES: android.assistant
package io.github.murtazanasir.mailr.live

import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.live.OpenEditors
import io.github.murtazanasir.mailr.ui.calendar.CalPane
import io.github.murtazanasir.mailr.ui.calendar.editing
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

/** One window per item: opening an item that is open already goes back to its screen. */
class OpenEditorsTest {
    @After fun tearDown() = OpenEditors.clear()

    /** draft.open: Open draft on a card whose draft is open already goes back to its composer. */
    @Test fun anOpenDraftBringsItsComposerForward() {
        val key = OpenEditors.key("draft", 4242L)
        assertEquals("draft:4242", key)
        assertNull("not open: a new composer", OpenEditors.target(key, listOf("main")))
        OpenEditors.register(key, "entry-2")
        assertEquals("entry-2", OpenEditors.target(key, listOf("main", "entry-2", "entry-3")))
        // A composer gone from the back stack (process death) is not a target.
        assertNull(OpenEditors.target(key, listOf("main")))
        assertNull("a new message has no draft yet", OpenEditors.target(null, listOf("main", "entry-2")))
    }

    @Test fun onlyTheOwnerForgetsItsKey() {
        val key = OpenEditors.key("draft", 7)
        OpenEditors.register(key, "a")
        OpenEditors.register(key, "b")  // the same draft got a newer composer
        OpenEditors.unregister(key, "a")
        assertEquals("b", OpenEditors.owner(key))
        OpenEditors.unregister(key, "b")
        assertNull(OpenEditors.owner(key))
    }

    @Test fun anOpenEventEditorStays() {
        val e = CalendarEvent(uid = "ev-1", summary = "Review", start = "2026-10-05T10:00:00Z")
        assertTrue(editing(CalPane.Edit(e, AppClock.localNow()), "ev-1"))
        assertFalse(editing(CalPane.Edit(e, AppClock.localNow()), "ev-2"))
        assertFalse(editing(CalPane.None, "ev-1"))
    }
}
