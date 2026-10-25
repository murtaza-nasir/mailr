package io.github.murtazanasir.mailr

import android.app.Application
import android.content.Intent
import android.net.Uri
import io.github.murtazanasir.mailr.data.api.Participant
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.ui.Destination
import io.github.murtazanasir.mailr.ui.compose.ComposeViewModel
import io.github.murtazanasir.mailr.ui.parseDeepLink
import io.github.murtazanasir.mailr.ui.util.Format
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.ZoneId
import java.time.ZonedDateTime

@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class UtilTest {
    private val now = ZonedDateTime.of(2026, 9, 28, 15, 0, 0, 0, ZoneId.systemDefault())

    @Test fun listDates() {
        assertEquals("Sep 21", Format.listDate(now.minusDays(7).toOffsetDateTime().toString(), now))
        assertEquals("9/21/25", Format.listDate(now.minusYears(1).minusDays(7).toOffsetDateTime().toString(), now))
        assertTrue(Format.listDate(now.minusHours(2).toOffsetDateTime().toString(), now).contains(":"))
        assertEquals("", Format.listDate(null, now))
    }

    @Test fun participants() {
        val ps = listOf(Participant("a@x.example", "Alice Chen"), Participant("me@x.example", "Me", me = true), Participant("b@x.example", "Bob Rivera"))
        assertEquals("Alice, me, Bob", Format.participants(ps, 3))
        assertEquals("Alice Chen", Format.participants(ps.take(1), 1))
        assertEquals("AC", Format.initials("Alice Chen", "a@x.example"))
        assertEquals("B", Format.initials(null, "bob@x.example"))
    }

    @Test fun htmlRoundTripAndQuoteSplit() {
        val html = Format.textToHtml("Hi <Bob> & co,\n\nSee you\nFriday")
        assertEquals("<p>Hi &lt;Bob&gt; &amp; co,</p><p>See you<br>Friday</p>", html)
        assertEquals("Hi <Bob> & co,\n\nSee you\nFriday", Format.htmlToText(html))
        val (mine, quote) = Format.splitQuote("<p>ok</p><div class=\"mailr_quote\">On Mon wrote</div>")
        assertEquals("<p>ok</p>", mine)
        assertTrue(quote!!.startsWith("<div class=\"mailr_quote\""))
    }

    @Test fun snippetsDropMarkdownMarks() {
        assertEquals("The Weekly Ledger Markets closed higher", Format.snippet("# The Weekly Ledger Markets closed **higher**"))
        assertEquals("Invoice #4821 is due", Format.snippet("Invoice #4821 is due"))
    }

    @Test fun labelColors() {
        val (bg, fg) = Format.labelColors("#1a73e8")!!
        assertTrue(bg.red < 0.2f && fg.red > 0.9f)
        assertNull(Format.labelColors(""))
    }

    @Test fun deepLinks() {
        fun link(s: String) = parseDeepLink(Intent(Intent.ACTION_VIEW, Uri.parse(s)), "mail.example.com", internal = true)
        assertEquals(Destination.Conversation(3066, 502, "nominations close"),
            link("https://mail.example.com/c/3066?m=502&q=nominations%20close"))
        assertEquals(Destination.Conversation(12), link("mailr://conversation/12"))
        assertEquals(Destination.Approvals, link("mailr://approvals"))
        assertEquals(Destination.ApprovalItem("41"), link("mailr://approvals/41"))
        assertEquals(Destination.ApprovalItem("o7"), link("mailr://approvals/o7"))
        val compose = link("mailto:alice@example.com?subject=Hi%20there&body=Line") as Destination.Compose
        assertEquals("alice@example.com", compose.args.to)
        assertEquals("Hi there", compose.args.subject)
        assertNull(link("https://example.com/other"))
    }

    @Test fun serverNormalisationAndEmails() {
        assertEquals("https://mail.example.com", SessionStore.normalizeServer(" mail.example.com/ "))
        assertEquals("http://mail.home.arpa", SessionStore.normalizeServer("http://mail.home.arpa"))
        assertTrue(ComposeViewModel.EMAIL.matches("a.b+c@example.co.uk"))
        assertFalse(ComposeViewModel.EMAIL.matches("not an email"))
    }

    /**
     * A cited event's source card shows its day with the time ("11:00 PM" alone sat
     * beside an answer about tomorrow). Asked at 15:49 on Sun 4 Oct 2026 in Chicago.
     */
    @Test fun citedEventsShowTheirDay() {
        val before = java.util.TimeZone.getDefault()
        java.util.TimeZone.setDefault(java.util.TimeZone.getTimeZone("America/Chicago"))
        try {
            val now = ZonedDateTime.of(2026, 10, 4, 15, 49, 0, 0, ZoneId.of("America/Chicago"))
            val tonight = Format.citeWhen("2026-10-05T04:00:00+00:00", now = now)
            assertTrue(tonight, tonight.startsWith("Today, 11:00") && tonight.endsWith("PM"))
            val monday = Format.citeWhen("2026-10-05T15:00:00+00:00", now = now)
            assertTrue(monday, monday.startsWith("Tomorrow, 10:00"))
            assertEquals("Sat 10 Oct", Format.citeWhen("2026-10-10", allDay = true, now = now))
        } finally {
            java.util.TimeZone.setDefault(before)
        }
    }
}
