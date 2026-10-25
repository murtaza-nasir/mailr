package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.ui.tasks.notesLine
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** A task's row shows the first line of its notes with Markdown links as their text: a task made
 *  from a list item names the list, not the long link back to it. */
class TaskNotesLineTest {
    @Test fun markdownLinksReadAsTheirText() {
        assertEquals("From the list Studio errands",
            notesLine("From the list [Studio errands](https://mail.example.com/notes/n/n8c7#i-01a1)\nmore"))
        assertEquals("Call Dana", notesLine("\n  \nCall Dana\n"))
        assertNull(notesLine(" \n"))
    }
}
