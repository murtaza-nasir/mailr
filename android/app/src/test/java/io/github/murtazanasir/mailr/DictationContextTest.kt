// ACT-SURFACES: android.ask android.assistant
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.data.voice.DictationContext
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** agent.dictate: what a clip is spoken into, sent as `context`; anything unknown sends none (local-only). */
class DictationContextTest {
    @Test fun namesTheChatOrTheItem() {
        assertEquals("chat:s1", DictationContext.of("s1", AskAbout.Conversation(3, "x")))
        assertEquals("none", DictationContext.of(null, null))
        assertEquals("note:n1", DictationContext.of(null, AskAbout.Note("n1", "x")))
        assertEquals("task:u1", DictationContext.of(null, AskAbout.Task("u1", "x")))
        assertEquals("task:e1", DictationContext.of(null, AskAbout.Event("e1", null, "x")))
    }

    @Test fun sendsNoneWhenItCannotTell() {
        assertNull(DictationContext.of(null, AskAbout.Conversation(3, "x")))
        assertNull(DictationContext.of(null, AskAbout.Note("t1", "x", kind = "table")))
        assertNull(DictationContext.of(null, null, conversation = 7))
    }
}
