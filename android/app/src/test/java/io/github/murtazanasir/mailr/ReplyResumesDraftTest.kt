// ACT-SURFACES: android.reader
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.data.api.Message
import io.github.murtazanasir.mailr.ui.compose.ComposeArgs
import io.github.murtazanasir.mailr.ui.compose.replyArgs
import org.junit.Assert.assertEquals
import org.junit.Test

/** ACT draft.resume @android.reader: Reply in a thread that has a saved draft opens that draft. */
class ReplyResumesDraftTest {
    private val incoming = Message(id = 1, subject = "Quote")
    private val draft = Message(id = 2, subject = "Re: Quote", draft = true)

    @Test fun replyOpensTheSavedDraft() {
        assertEquals(ComposeArgs(draftId = 2), replyArgs(listOf(incoming, draft), incoming, "reply"))
        assertEquals(ComposeArgs(draftId = 2), replyArgs(listOf(incoming, draft), incoming, "forward"))
    }

    @Test fun withoutADraftReplyStartsNew() {
        assertEquals(ComposeArgs(replyToMessageId = 1, mode = "reply_all"), replyArgs(listOf(incoming), incoming, "reply_all"))
    }
}
