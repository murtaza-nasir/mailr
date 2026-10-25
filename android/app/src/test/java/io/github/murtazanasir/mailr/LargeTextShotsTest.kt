package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onRoot
import androidx.paging.PagingData
import androidx.paging.compose.collectAsLazyPagingItems
import io.github.murtazanasir.mailr.data.api.MessageBody
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.conversation.BodyState
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.mail.ListActions
import io.github.murtazanasir.mailr.ui.mail.MailListPane
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.coroutines.flow.flowOf
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The phone's key screens at the largest system font size (Settings → Display → Font size, 200 %):
 * the inbox, a conversation and the task list. Rows grow with the text instead of clipping it.
 * Output: app/screenshots/phone-light-font200-<screen>.png
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class LargeTextShotsTest {
    @get:Rule val compose = createComposeRule()

    @Before fun setUp() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-notnight-xxhdpi")
        RuntimeEnvironment.setFontScale(2f)
    }

    @After fun reset() = RuntimeEnvironment.setFontScale(1f)

    private fun shot(name: String, content: @Composable () -> Unit) {
        compose.setContent {
            MailrTheme(ThemeMode.LIGHT, dynamic = false) { Surface(Modifier.fillMaxSize()) { content() } }
        }
        compose.onRoot().captureRoboImage("screenshots/phone-light-font200-$name.png")
    }

    private val noList = ListActions({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> }, {}, { _, _ -> }, {}, {}, {})
    private val noConv = ConversationActions(back = {}, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
        openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {}, mailto = {}, retry = {})

    @Test fun inbox() = shot("inbox") {
        val items = flowOf(PagingData.from(Fixtures.conversations)).collectAsLazyPagingItems()
        MailListPane(MailView(), items, emptyList(), null, Fixtures.labels, emptySet(), null, Prefs(),
            "Alex Morgan", "me@example.com", false, 0, SnackbarHostState(), true, noList)
    }

    @Test fun conversation() = shot("conversation") {
        val html = "<p>Dear colleague,</p><p>Award nominations close Sep 30.</p><p>Best regards,<br>Program Chair</p>"
        ConversationScreen(Fixtures.detail, null, Fixtures.labels, setOf(502L),
            mapOf(502L to BodyState.Ready(MessageBody(html = html, source = "html", remoteImages = true), false)),
            null, null, false, SnackbarHostState(), noConv)
    }

    @Test fun tasks() = shot("tasks") {
        val noon = AppClock.today().atTime(12, 20).atZone(java.time.ZoneId.systemDefault()).toInstant().toEpochMilli()
        val groups = io.github.murtazanasir.mailr.ui.tasks.TaskGroups.build(io.github.murtazanasir.mailr.ui.tasks.TaskView.SmartView(io.github.murtazanasir.mailr.ui.tasks.Smart.ALL),
            Fixtures.tasks, noon, java.time.ZoneId.systemDefault())
        io.github.murtazanasir.mailr.ui.tasks.TaskGroupList(groups, noon, emptyList(), emptySet(),
            io.github.murtazanasir.mailr.ui.tasks.SwipeAction.COMPLETE to io.github.murtazanasir.mailr.ui.tasks.SwipeAction.RESCHEDULE,
            io.github.murtazanasir.mailr.ui.tasks.RowStyle(expandSubtasks = true),
            io.github.murtazanasir.mailr.ui.tasks.TaskListActions({}, {}, {}, { _, _ -> }, {}, { _, _, _ -> }, { _, _ -> }, {}, {}, {}))
    }
}
