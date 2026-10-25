package io.github.murtazanasir.mailr.ui.present

import android.app.Application
import io.github.murtazanasir.mailr.data.api.MailrJson
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Server texts kept as a key: a msg renders from strings_server.xml, the English is the fallback. */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ServerTextTest {
    private fun msg(json: String) = MailrJson.decodeFromString(Msg.serializer(), json)

    @Test fun rendersNestedMsgsAndPlurals() {
        val m = msg("""{"key":"approvals.mail.trash.ask","params":{"what":{"key":"approvals.what.messages","params":{"count":6}}}}""")
        assertEquals("Move 6 messages to Trash", ServerText.text(m, "x"))
        assertEquals("1 message", ServerText.text(msg("""{"key":"approvals.what.messages","params":{"count":1}}"""), "x"))
        assertEquals("Send to Marco Silva", ServerText.text(msg("""{"key":"approvals.send.title","params":{"who":"Marco Silva"}}"""), "x"))
    }

    @Test fun fallsBackToTheEnglish() {
        assertEquals("Stored", ServerText.text(msg("""{"key":"approvals.no.such.key","params":{}}"""), "Stored"))
        assertEquals("Stored", ServerText.text(null as Msg?, "Stored"))
        val ev = MailrJson.parseToJsonElement("""{"key":"approvals.send.title","params":{"who":{"key":"nope","params":{}}}}""")
        assertEquals("Stored", ServerText.text(ev.jsonObject, "Stored"))
    }

    @Test fun aDisplayIsLocalizedWithItsPlainText() {
        val d = MailrJson.decodeFromString(Display.serializer(), """{"v":1,"icon":"send","title":"Send to A","summary":null,
            "danger":false,"reason":null,"text":"Send to A\nTo: a@example.org",
            "title_msg":{"key":"approvals.send.title","params":{"who":"A"}},
            "fields":[{"label":"To","label_msg":{"key":"approvals.field.to","params":{}},
                       "value":{"t":"text","text":"a@example.org"}}]}""")
        val l = d.localized()
        assertEquals("Send to A", l.title)
        assertEquals("To", l.fields[0].label)
        assertEquals("Send to A\nTo: a@example.org", l.text)
    }
}
