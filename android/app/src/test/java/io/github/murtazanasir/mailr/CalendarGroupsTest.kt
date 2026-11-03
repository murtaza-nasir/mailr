package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.CollectionSource
import io.github.murtazanasir.mailr.data.api.CollectionSync
import io.github.murtazanasir.mailr.data.api.ConnectorSyncStatus
import io.github.murtazanasir.mailr.data.api.PhoneInfo
import io.github.murtazanasir.mailr.data.api.SharedInfo
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.calendar.AccountErrorBanner
import io.github.murtazanasir.mailr.ui.calendar.CalendarGroups
import io.github.murtazanasir.mailr.ui.calendar.SyncProblems
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The calendar drawer's groups (CalendarGroups: one heading per source) and calendar sync failures
 * in plain words (SyncProblems), with the technical text behind Details on the banner.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class CalendarGroupsTest {
    @get:Rule val compose = createComposeRule()

    private val studio = CollectionSource("account", name = "Studio", host = "cal.example.org")
    private val cols = listOf(
        CalendarCollection("phone:1", "Work phone", events = true, connectorId = 9, phone = PhoneInfo("1", "Pixel 9"),
            source = CollectionSource("phone", device = "Pixel 9")),
        CalendarCollection("/a/default/", "Default Calendar", events = true, connectorId = 3, source = studio),
        CalendarCollection("/b/cal/", "Calendar", events = true, connectorId = 1, source = CollectionSource("builtin")),
        CalendarCollection("/g/family/", "Family", events = true, connectorId = 1, shared = SharedInfo("group", group = "Family"),
            source = CollectionSource("shared")),
        CalendarCollection("/a/family/", "family", events = true, connectorId = 3, source = studio),
        CalendarCollection("webcal:7", "Holidays", events = true, connectorId = 7, source = CollectionSource("link")),
    )

    @Test fun groupsBySourceInAFixedOrder() {
        val groups = CalendarGroups.group(cols, emptyList())
        assertEquals(listOf("builtin", "account", "shared", "phone", "link"), groups.map { it.kind })
        assertEquals(listOf("Mailr calendar", "Studio · cal.example.org", "Shared with you", "Calendars from your phone · Pixel 9", "Calendar links"),
            groups.map { it.heading })
        assertEquals(listOf("Default Calendar", "family"), groups[1].collections.map { it.name })
        // New calendars go to the Mailr calendar or an account, never to a shared, phone or link group.
        assertEquals(listOf("builtin", "account"), groups.filter { it.canCreate }.map { it.kind })
    }

    @Test fun anAccountFailureStaysWithItsAccount() {
        val acc = listOf(
            ConnectorSyncStatus(3, "Studio", "error", "PROPFIND https://cal.example.org/dav/: not found", errorKind = "not_found", errorHost = "cal.example.org"),
            ConnectorSyncStatus(1, "Calendar", "idle"))
        val groups = CalendarGroups.group(cols, acc)
        val studioGroup = groups.single { it.kind == "account" }
        assertEquals("not_found", studioGroup.errorKind)
        assertEquals("cal.example.org", studioGroup.errorHost)
        assertEquals("PROPFIND https://cal.example.org/dav/: not found", studioGroup.error)
        assertFalse(studioGroup.authFailed)
        assertTrue(groups.filter { it.kind != "account" }.all { it.error == null })
    }

    @Test fun theBuiltInAccountsFailureIsNotSharedCalendars() {
        val acc = listOf(ConnectorSyncStatus(1, "Calendar", "error", "REPORT https://mail.example.org/dav/: HTTP 503", errorKind = "server",
            errorHost = "mail.example.org"))
        val groups = CalendarGroups.group(cols, acc)
        assertEquals("server", groups.single { it.kind == "builtin" }.errorKind)
        assertNull(groups.single { it.kind == "shared" }.error)
    }

    @Test fun aCalendarsOwnFailureComesWithItsKind() {
        val bad = cols.map { if (it.url == "/a/family/") it.copy(sync = CollectionSync("backoff", error = "listed only part", errorKind = "partial")) else it }
        val g = CalendarGroups.group(bad, emptyList()).single { it.kind == "account" }
        assertEquals("partial", g.errorKind)
        assertEquals("cal.example.org", g.errorHost)
    }

    @Test fun anOlderServerKeepsTheGroupsByAccount() {
        val old = listOf(CalendarCollection("/w/", "Work", events = true, connectorId = 1), CalendarCollection("/h/", "Home", events = true, connectorId = 1))
        val one = CalendarGroups.group(old, emptyList())
        assertEquals(listOf("Calendars"), one.map { it.name })
        val two = CalendarGroups.group(old + CalendarCollection("/x/", "X", events = true, connectorId = 2),
            listOf(ConnectorSyncStatus(2, "Fastmail", "error", "Sign-in failed. Check the password.", errorKind = "auth")))
        assertEquals(listOf("Account 1", "Fastmail"), two.map { it.name })
        assertTrue(two[1].authFailed)
    }

    @Test fun everyKindHasItsOwnPlainText() {
        assertEquals("The calendar server at cal.example.org no longer has calendars at this address. It may have moved or been switched off. " +
            "Check the address, or disconnect this account if you no longer use it.", SyncProblems.text("not_found", "cal.example.org"))
        val texts = SyncProblems.KINDS.map { SyncProblems.text(it, "cal.example.org") }
        assertEquals(SyncProblems.KINDS.size, texts.toSet().size)
        texts.forEach { assertTrue(it, "cal.example.org" in it && "PROPFIND" !in it) }
        assertEquals(SyncProblems.text("unknown", "Studio"), SyncProblems.text("something-new", null, "Studio"))
    }

    @Test fun theBannerSaysWhatToDoAndKeepsTheTechnicalTextBehindDetails() {
        val raw = "PROPFIND https://cal.example.org/dav/: not found"
        compose.setContent { MaterialTheme { AccountErrorBanner("Studio", raw, kind = "not_found", host = "cal.example.org") {} } }
        compose.onNodeWithText("no longer has calendars at this address", substring = true).assertExists()
        compose.onNodeWithText(raw, substring = true).assertDoesNotExist()
        compose.onNodeWithText("Details").performClick()
        compose.onNodeWithText(raw, substring = true).assertExists()
    }
}
