// ACT-SURFACES: android.editor android.reader
package io.github.murtazanasir.mailr

import android.app.Application
import android.content.Intent
import android.net.Uri
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.ui.Destination
import io.github.murtazanasir.mailr.ui.compose.Mailto
import io.github.murtazanasir.mailr.ui.parseDeepLink
import io.github.murtazanasir.mailr.ui.parseMailtoArgs
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * compose.deeplink: mailto: links from other apps and from message bodies open the composer filled in
 * (ui/compose/Mailto.kt). The cases are spec/cases/mailto-cases.yaml (the copy in test
 * resources); the web runs the same file (frontend/src/lib/mailto.test.ts).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class MailtoTest {
    private fun show(list: List<Address>) = list.map { if (it.name.isNullOrBlank()) it.email else "${it.name} <${it.email}>" }

    @Test fun mailtoCases() {
        val file = io.github.murtazanasir.mailr.ui.calendar.FlowYaml.read("interaction/mailto-cases.yaml")
        file["cases"]!!.jsonArray.map { it.jsonObject }.forEach { c ->
            val id = c["id"]!!.jsonPrimitive.content
            val f = Mailto.parse(c["url"]!!.jsonPrimitive.content)
            if (c["none"]?.jsonPrimitive?.booleanOrNull == true) {
                assertNull(id, f)
                return@forEach
            }
            fun list(k: String) = c[k]!!.jsonArray.map { it.jsonPrimitive.content }
            assertEquals("$id to", list("to"), show(f!!.to))
            assertEquals("$id cc", list("cc"), show(f.cc))
            assertEquals("$id bcc", list("bcc"), show(f.bcc))
            assertEquals("$id subject", c["subject"]!!.jsonPrimitive.content, f.subject)
            assertEquals("$id body", c["body"]!!.jsonPrimitive.content, f.body)
        }
    }

    @Test fun aLinkFillsAtMostFiftyRecipientsAndFiftyThousandCharacters() {
        // The web's twin: frontend/src/lib/mailto.test.ts.
        val many = (0 until 40).joinToString(",") { "u$it@example.com" }
        val f = Mailto.parse("mailto:$many?cc=$many&bcc=" + (0 until 40).joinToString(",") { "b$it@example.com" } +
            "&body=" + "x".repeat(200_000))!!
        assertEquals(Mailto.MAX_RECIPIENTS, f.to.size + f.cc.size + f.bcc.size)
        assertEquals(40, f.to.size)
        assertEquals(10, f.cc.size)
        assertEquals(0, f.bcc.size)
        assertEquals(Mailto.MAX_BODY, f.body.length)
        val more = Mailto.withExtras(f, listOf("late@example.com"), null, null, null, null)
        assertEquals(Mailto.MAX_RECIPIENTS, more.to.size + more.cc.size + more.bcc.size)
    }

    @Test fun aMailtoIntentFillsEveryField() {
        val uri = Uri.parse("mailto:%22Lee,%20Dana%22%20%3Cdana@example.com%3E?cc=erin@example.com&bcc=frank@example.com" +
            "&subject=Tom%20%26%20Jerry&body=Line%20one%0D%0ALine%20two")
        val args = (parseDeepLink(Intent(Intent.ACTION_SENDTO, uri)) as Destination.Compose).args
        assertEquals(listOf(Address("dana@example.com", "Lee, Dana")), Mailto.parseAddresses(args.to))
        assertEquals(listOf(Address("erin@example.com")), Mailto.parseAddresses(args.cc))
        assertEquals(listOf(Address("frank@example.com")), Mailto.parseAddresses(args.bcc))
        assertEquals("Tom & Jerry", args.subject)
        assertEquals("Line one\nLine two", args.body)
    }

    @Test fun extrasBesideTheLinkCount() {
        val i = Intent(Intent.ACTION_SENDTO, Uri.parse("mailto:"))
            .putExtra(Intent.EXTRA_EMAIL, arrayOf("alice@example.com"))
            .putExtra(Intent.EXTRA_CC, arrayOf("bob@example.org"))
            .putExtra(Intent.EXTRA_SUBJECT, "From an app")
            .putExtra(Intent.EXTRA_TEXT, "Hello")
        val args = (parseDeepLink(i) as Destination.Compose).args
        assertEquals("alice@example.com", args.to)
        assertEquals("bob@example.org", args.cc)
        assertEquals("From an app", args.subject)
        assertEquals("Hello", args.body)
        // The link's own subject wins over the extra.
        val j = Intent(Intent.ACTION_SENDTO, Uri.parse("mailto:a@example.com?subject=Link")).putExtra(Intent.EXTRA_SUBJECT, "Extra")
        assertEquals("Link", (parseDeepLink(j) as Destination.Compose).args.subject)
    }

    @Test fun aBodyLinkKeepsPlusAndAmpersands() {
        // The reader's mailto: links (MessageWebView → parseMailtoArgs).
        val args = parseMailtoArgs(Uri.parse("mailto:a+news@example.com?body=1%2B1%20%26%202"))
        assertEquals("a+news@example.com", args.to)
        assertEquals("1+1 & 2", args.body)
    }
}
