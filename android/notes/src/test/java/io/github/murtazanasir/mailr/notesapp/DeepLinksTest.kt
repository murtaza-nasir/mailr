// ACT-SURFACES: android.notesapp
package io.github.murtazanasir.mailr.notesapp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The Notes app's addresses (note.open_app opens them from the mailr app). */
class DeepLinksTest {
    private fun t(link: String) = DeepLinks.parse(link)?.target

    @Test fun customSchemeLongAndShortForms() {
        assertEquals(NotesTarget.Note("n12"), t("mailr-notes://note/n12"))
        assertEquals(NotesTarget.Note("n12", "0190-ab"), t("mailr-notes://note/n12/item/0190-ab"))
        assertEquals(NotesTarget.Note("n12", "i9"), t("mailr-notes://n/n12/i/i9"))
        assertEquals(NotesTarget.Table("t3"), t("mailr-notes://table/t3"))
        assertEquals(NotesTarget.Row("t3", "r-1"), t("mailr-notes://table/t3/row/r-1"))
        assertEquals(NotesTarget.Row("t3", "r-1"), t("mailr-notes://t/t3/r/r-1"))
        assertEquals(NotesTarget.Row("t3", "new"), t(DeepLinks.tableUri("t3", "new")))
    }

    @Test fun newAndSetupLinks() {
        assertEquals(NotesTarget.New("text"), t("mailr-notes://new?kind=text"))
        assertEquals(NotesTarget.New("checklist"), t("mailr-notes://new?kind=checklist"))
        assertEquals(NotesTarget.New("table"), t("mailr-notes://new?kind=table"))
        // new?kind=list&list=<id> opens that list.
        assertEquals(NotesTarget.Note("n5"), t("mailr-notes://new?kind=list&list=n5"))
        assertEquals(NotesTarget.Setup("https://mail.example.com"), t("mailr-notes://setup?server=https%3A%2F%2Fmail.example.com"))
        // The mailr app's setup link is not the Notes app's.
        assertNull(t("mailr://setup?server=https%3A%2F%2Fmail.example.com"))
    }

    @Test fun webRoutesCarryTheirHost() {
        val l = DeepLinks.parse("https://mail.example.com/notes/n/n12#i-0190-ab")!!
        assertEquals(NotesTarget.Note("n12", "0190-ab"), l.target)
        assertEquals("mail.example.com", l.host)
        assertEquals(NotesTarget.Row("t3", "r9"), t("https://mail.example.com/tables/t/t3/r/r9"))
        assertEquals(NotesTarget.Table("t3"), t("https://mail.example.com/tables/t/t3"))
        assertEquals(NotesTarget.Note("n12"), t("https://mail.example.com/notes/n/n12"))
    }

    @Test fun anythingElseIsNotALinkOfOurs() {
        listOf("", "http://mail.example.com/notes/n/n1", "mailr-notes://note/", "mailr-notes://note/a%20b", "mailr-notes://box/n1",
            "https://mail.example.com/mail/inbox", "mailr-notes://new?kind=folder", "javascript:alert(1)")
            .forEach { assertNull(it, DeepLinks.parse(it)) }
    }
}
