package io.github.murtazanasir.mailr.ui.conversation

import io.github.murtazanasir.mailr.data.api.Message
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * A draft changed elsewhere (the assistant's edit, applied after an approval) keeps its message id:
 * the reader sees the change only as a new Date, subject or snippet in the refreshed conversation,
 * and loads that draft's body again. Other messages, and drafts seen for the first time, load nothing.
 */
class DraftRevisionsTest {
    private val mail = Message(id = 1, subject = "Re: Venue", date = "2026-10-01T09:00:00Z", snippet = "Thanks for the list.")
    private val draft = Message(id = 2, subject = "Re: Venue", date = "2026-10-01T10:00:00Z", snippet = "See you Thursday.", draft = true)

    @Test fun aDraftWhoseTextChangedIsReloaded() {
        val seen = draftRevisions(listOf(mail, draft))
        val edited = draft.copy(date = "2026-10-01T10:00:41Z", snippet = "See you Friday.")
        assertEquals(listOf(2L), changedDrafts(seen, listOf(mail, edited)))
    }

    @Test fun aSnippetChangeAloneCounts() {
        val seen = draftRevisions(listOf(draft))
        assertEquals(listOf(2L), changedDrafts(seen, listOf(draft.copy(snippet = "See you Friday."))))
    }

    @Test fun nothingChangedLoadsNothing() {
        val seen = draftRevisions(listOf(mail, draft))
        assertEquals(emptyList<Long>(), changedDrafts(seen, listOf(mail.copy(seen = false), draft)))
    }

    @Test fun aNewDraftOrAnOrdinaryMessageIsNotAChange() {
        assertEquals(emptyList<Long>(), changedDrafts(emptyMap(), listOf(mail, draft)))
        assertEquals(mapOf(2L to draftRevision(draft)!!), draftRevisions(listOf(mail, draft)))
    }
}
