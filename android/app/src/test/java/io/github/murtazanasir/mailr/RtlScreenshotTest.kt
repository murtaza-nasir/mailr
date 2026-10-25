// ACT-SURFACES: android.list android.reader android.editor android.assistant android.drawer android.calendar android.tasks android.settings
// Right to left: the key screens in ar-XB, Android's
// right-to-left pseudo-language (debug builds: pseudoLocalesEnabled), on a phone, light and dark.
// The layout mirrors (start is on the right), directional icons are the AutoMirrored ones, swipe
// backgrounds and the calendar's time gutter sit on the start side, and text the user wrote keeps
// its own direction. Output: app/screenshots/rtl-<theme>-<screen>.png. Demo data only.
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.LayoutDirection
import androidx.paging.PagingData
import androidx.paging.compose.collectAsLazyPagingItems
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.data.api.Identity
import io.github.murtazanasir.mailr.data.api.MessageBody
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.assistant.AssistantCallbacks
import io.github.murtazanasir.mailr.ui.assistant.AssistantScreen
import io.github.murtazanasir.mailr.ui.assistant.AssistantState
import io.github.murtazanasir.mailr.ui.assistant.ChatTurn
import io.github.murtazanasir.mailr.ui.assistant.TurnItem
import io.github.murtazanasir.mailr.ui.compose.ComposeCallbacks
import io.github.murtazanasir.mailr.ui.compose.ComposeScreen
import io.github.murtazanasir.mailr.ui.compose.ComposeState
import io.github.murtazanasir.mailr.ui.conversation.BodyState
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.mail.ListActions
import io.github.murtazanasir.mailr.ui.mail.MailDrawer
import io.github.murtazanasir.mailr.ui.mail.MailListPane
import io.github.murtazanasir.mailr.ui.settings.SettingsCallbacks
import io.github.murtazanasir.mailr.ui.settings.SettingsScreen
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.coroutines.flow.flowOf
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.ZoneId

@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class RtlScreenshotTest(private val dark: Boolean) {

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))

        /** ar-XB with its right-to-left layout direction, on a phone. */
        fun qualifiers(dark: Boolean) = "ar-rXB-ldrtl-w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi"
    }

    @get:Rule val compose = createComposeRule()
    private val mode get() = if (dark) "dark" else "light"

    @Before fun setUp() {
        RuntimeEnvironment.setQualifiers(qualifiers(dark))
    }

    private fun shot(name: String, content: @Composable () -> Unit) {
        var direction: LayoutDirection? = null
        compose.setContent {
            direction = LocalLayoutDirection.current
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) { content() }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/rtl-$mode-$name.png")
        assertEquals("the screen is laid out right to left", LayoutDirection.Rtl, direction)
    }

    private val noList = ListActions({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> }, {}, { _, _ -> }, {}, {}, {})
    private val noConv = ConversationActions(back = {}, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
        openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {}, mailto = {}, retry = {})

    @Test fun inbox() = shot("inbox") {
        val items = flowOf(PagingData.from(Fixtures.conversations)).collectAsLazyPagingItems()
        MailListPane(MailView(), items, emptyList(), null, Fixtures.labels, emptySet(), null, Prefs(),
            "Alex Morgan", "me@example.com", false, 0, SnackbarHostState(), true, noList)
    }

    @Test fun drawer() = shot("drawer") {
        MailDrawer(Fixtures.labels, MailView(), null, emptyMap(), "me@example.com", 2, {}, {}, {})
    }

    @Test fun conversation() = shot("conversation") {
        // An Arabic paragraph and an English one: each keeps its own direction in the body.
        val html = "<p>مرحبا يا فريق! الاجتماع غداً الساعة العاشرة.</p><p>The agenda is attached. See you there.</p>"
        ConversationScreen(Fixtures.detail, null, Fixtures.labels, setOf(502L),
            mapOf(502L to BodyState.Ready(MessageBody(html = html, source = "html", remoteImages = true), false)),
            null, null, false, SnackbarHostState(), noConv)
    }

    @Test fun compose() = shot("compose") {
        ComposeScreen(
            ComposeState(loading = false, identities = listOf(Identity(1, "me@example.com", "Alex Morgan", isDefault = true)),
                identityId = 1, to = listOf(Address("alice@example.com", "Alice Chen"), Address("dana@example.org", "دانة")),
                subject = "Re: Quarterly plan",
                blocks = io.github.murtazanasir.mailr.ui.compose.ComposeBody.ofText("Friday at 10 works for me.\n\nشكراً!"),
                title = "Reply", saveStatus = "Saved"),
            SnackbarHostState(),
            ComposeCallbacks({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> true }, { _, _ -> }, { _, _ -> }, { _, _ -> }, {}, {}, {}),
        )
    }

    @Test fun assistant() = shot("assistant") {
        AssistantScreen(
            AssistantState(turns = listOf(
                ChatTurn("user", "What needs a reply from me this week?"),
                ChatTurn("assistant", items = listOf(
                    TurnItem.Text(0, "", "Two things are waiting:\n\n- **SPADS award nominations** close Sep 30 [1](mailr-cite:1).\n" +
                        "- رد على دانة بخصوص الميزانية [2](mailr-cite:2).")),
                    citations = Fixtures.citations, startedAt = 0, finishedAt = 4_200),
            )),
            AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {}),
        )
    }

    private val today = AppClock.today()
    private val calEvents get() = io.github.murtazanasir.mailr.ui.calendar.dayEvents(Fixtures.events, emptyList())
    private val noon = today.atTime(12, 20).atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()

    @Test fun calendarWeek() = shot("calendar-week") {
        val start = io.github.murtazanasir.mailr.ui.calendar.weekStart(today)
        io.github.murtazanasir.mailr.ui.calendar.TimeGrid((0L until 7L).map { start.plusDays(it) }, calEvents, noon, null, {}, {}, {})
    }

    @Test fun calendarMonth() = shot("calendar-month") {
        io.github.murtazanasir.mailr.ui.calendar.MonthView(today, calEvents, false, {}, {}, Modifier.fillMaxSize())
    }

    /** The phone month with titles in the days (note #14): chips and "+n" run right to left, the agenda's times on the start side. */
    @Test fun calendarMonthBusy() = shot("calendar-month-busy") { BusyMonth.PhoneMonth() }

    @Test fun tasks() = shot("tasks") {
        val groups = io.github.murtazanasir.mailr.ui.tasks.TaskGroups.build(io.github.murtazanasir.mailr.ui.tasks.TaskView.SmartView(io.github.murtazanasir.mailr.ui.tasks.Smart.ALL),
            Fixtures.tasks, noon, ZoneId.systemDefault())
        io.github.murtazanasir.mailr.ui.tasks.TaskGroupList(groups, noon, emptyList(), emptySet(),
            io.github.murtazanasir.mailr.ui.tasks.SwipeAction.COMPLETE to io.github.murtazanasir.mailr.ui.tasks.SwipeAction.RESCHEDULE,
            io.github.murtazanasir.mailr.ui.tasks.RowStyle(expandSubtasks = true),
            io.github.murtazanasir.mailr.ui.tasks.TaskListActions({}, {}, {}, { _, _ -> }, {}, { _, _, _ -> }, { _, _ -> }, {}, {}, {}))
    }

    @Test fun settings() = shot("settings") {
        SettingsScreen(Prefs(), "Alex Morgan", "me@example.com", "https://mail.example.com", emptyList(),
            SettingsCallbacks({}, {}, {}, {}, {}))
    }
}
