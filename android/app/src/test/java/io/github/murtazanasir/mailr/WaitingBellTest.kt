// ACT-SURFACES: android.approvals
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.OutboxRow
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.data.reminders.Kind
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.approvals.ApprovalsScreen
import io.github.murtazanasir.mailr.ui.approvals.ApprovalsTracker
import io.github.murtazanasir.mailr.ui.approvals.WaitingBell
import io.github.murtazanasir.mailr.ui.approvals.WaitingCounts
import io.github.murtazanasir.mailr.ui.approvals.reminderLine
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.builtins.ListSerializer
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.net.URLDecoder
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.concurrent.CopyOnWriteArrayList

/**
 * "Waiting for you" on Android, with the web bell's sections (features/waiting): Approvals with Allow all,
 * Not sent with Retry and Open Outbox (draft.retry_send, draft.outbox), and Reminders now and missed
 * (notification.open: a tap opens the reminder's sheet; notification.open_stack: Review all). The bell
 * in the inbox counts all three: red while an approval or a failed send waits, grey when only missed
 * reminders are left.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class WaitingBellTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()
    private val app: Application = ApplicationProvider.getApplicationContext()
    private var server: MockWebServer? = null
    private var scope: CoroutineScope? = null
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    private val zone = ZoneId.of("America/Chicago")
    private val now = ZonedDateTime.of(2026, 10, 7, 10, 0, 0, 0, zone)

    @After fun tearDown() {
        scope?.cancel()
        graphs.close()
        server?.shutdown()
    }

    private val failed = OutboxRow(41, "failed", "Re: Quarterly plan", listOf("alice@example.com"), error = "550 5.1.1 user unknown", conversationId = 3050)

    private fun reminder(key: String, kind: String, title: String, at: ZonedDateTime, missed: Boolean = false) =
        ReminderEntity(key, kind, "uid-$key", null, title, "", at.toInstant().toEpochMilli(), at.toInstant().toEpochMilli(),
            at.toInstant().toEpochMilli(), null, RState.SHOWN, missed = missed, updatedAt = 0)

    private val reminders = listOf(
        reminder("e1", Kind.EVENT, "Team sync", now.plusMinutes(10)),
        reminder("t1", Kind.TASK, "Send the budget to Alice", now.minusHours(20), missed = true),
    )

    // The screen reads the phone's clock: its reminders are dated from now.
    private val liveNow = AppClock.now(ZoneId.systemDefault()).withSecond(0).withNano(0)
    private val liveReminders = listOf(
        reminder("e1", Kind.EVENT, "Team sync", liveNow.plusMinutes(10)),
        reminder("t1", Kind.TASK, "Send the budget to Alice", liveNow.minusDays(1).withHour(17).withMinute(0), missed = true),
    )

    @Test fun theCountsAndTheirWordsMatchTheWeb() {
        assertEquals("Nothing waiting", WaitingCounts().label)
        assertEquals("Waiting for you: 1 approval", WaitingCounts(approvals = 1).label)
        assertEquals("Waiting for you: 2 approvals, 1 not sent, 3 reminders (1 missed)", WaitingCounts(2, 1, 3, 1).label)
        assertEquals("Waiting for you: 2 reminders (2 missed)", WaitingCounts(reminders = 2, missed = 2).label)
        assertTrue(WaitingCounts(notSent = 1, reminders = 1, missed = 1).urgent)
        assertTrue(WaitingCounts(reminders = 2, missed = 2).quiet)
        assertFalse(WaitingCounts(reminders = 2, missed = 1).quiet)
        assertFalse(WaitingCounts(approvals = 1, reminders = 1, missed = 1).quiet)
        assertEquals("Starts 10:10 AM", reminderLine(reminders[0], now))
        assertEquals("Missed · Due yesterday 2:00 PM", reminderLine(reminders[1], now))
    }

    @Test fun theBellIsRedForApprovalsAndFailedSendsAndGreyForMissedRemindersOnly() {
        var c by mutableStateOf(WaitingCounts(notSent = 1, reminders = 2, missed = 2))
        var opened = 0
        compose.setContent { MaterialTheme { WaitingBell(c) { opened++ } } }
        compose.onNodeWithContentDescription("Waiting for you: 1 not sent, 2 reminders (2 missed)").performClick()
        assertEquals(1, opened)
        compose.onNodeWithTag("waiting-badge-red", useUnmergedTree = true).assertExists()
        compose.onNodeWithText("3", useUnmergedTree = true).assertExists()
        c = WaitingCounts(reminders = 2, missed = 2)
        compose.onNodeWithTag("waiting-badge-quiet", useUnmergedTree = true).assertExists()
        c = WaitingCounts()
        compose.waitForIdle()
        assertEquals(0, compose.onAllNodesWithTag("waiting-bell").fetchSemanticsNodes().size)
    }

    private var dark by mutableStateOf(false)

    private fun screen(picked: MutableList<String>) {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-xxhdpi")
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
                    ApprovalsScreen(Fixtures.approvals.take(1), emptyList(), null, false, SnackbarHostState(), {}, {}, { _, _, _ -> },
                        notSent = listOf(failed), onRetry = { picked += "retry:${it.id}" }, onOutbox = { picked += "outbox" },
                        reminders = liveReminders, onReminder = { picked += "open:${it.key}" }, onReviewAll = { picked += "review" })
                }
            }
        }
    }

    // ACT draft.retry_send, ACT draft.outbox, ACT notification.open, ACT notification.open_stack
    @Test fun theScreenHasApprovalsNotSentAndReminders() {
        val picked = mutableListOf<String>()
        screen(picked)
        for (d in listOf(false, true)) {
            dark = d
            compose.onNodeWithText("Approvals · 1").assertExists()
            compose.onNodeWithText("Not sent · 1").assertExists()
            compose.onNodeWithText("To alice@example.com · An address was refused. Check the recipients.").assertExists()
            compose.onNodeWithText("Reminders · 2").assertExists()
            compose.onNodeWithText("Missed").assertExists()
            compose.onRoot().captureRoboImage("screenshots/waiting-${if (dark) "dark" else "light"}.png")
        }
        compose.onNodeWithText("Retry").performClick()
        compose.onNodeWithText("Open Outbox").performClick()
        compose.onNodeWithText("Team sync").performClick()
        compose.onNodeWithText("Review all").performClick()
        assertEquals(listOf("retry:41", "outbox", "open:e1", "review"), picked)
    }

    private fun until(what: String, cond: () -> Boolean) = eventually(what, idle = {}, check = cond)

    /** The tracker reads the failed sends with the approvals, counts the reminders showing, and Retry posts. */
    @Test fun theTrackerCountsAllThreeAndRetries() {
        val s = MockWebServer()
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request
                val path = URLDecoder.decode(request.path.orEmpty(), "UTF-8")
                return when {
                    path == "/api/v1/approvals?status=pending" -> MockResponse().setBody(
                        """{"items":${MailrJson.encodeToString(ListSerializer(io.github.murtazanasir.mailr.data.api.Approval.serializer()), Fixtures.approvals)}}""")
                    path.startsWith("/api/v1/approvals?") -> MockResponse().setBody("[]")
                    path == "/api/v1/outbox?active=true" -> MockResponse().setBody(MailrJson.encodeToString(ListSerializer(OutboxRow.serializer()),
                        listOf(failed, OutboxRow(42, "queued", "Later"))))
                    path == "/api/v1/outbox/41/retry" -> MockResponse().setBody("""{"outbox":{"id":41,"status":"queued"}}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        s.start()
        server = s
        SessionStore(app).apply { server = s.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        val g = graphs.graph(app)
        val sc = graphs.scope(Dispatchers.Unconfined).also { scope = it }
        val t = ApprovalsTracker(g, emptyFlow(), sc)
        runBlocking { t.refresh(seed = true) }
        assertEquals(listOf(41L), t.notSent.value.map { it.id })
        runBlocking { g.db.reminders().put(reminders) }
        until("reminders counted") { t.counts.value.reminders == 2 }
        assertEquals(WaitingCounts(approvals = 2, notSent = 1, reminders = 2, missed = 1), t.counts.value)
        t.retry(failed)
        until("retry") { requests.any { it.path == "/api/v1/outbox/41/retry" && it.method == "POST" } }
    }
}
