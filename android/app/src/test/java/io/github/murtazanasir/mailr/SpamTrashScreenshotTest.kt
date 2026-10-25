package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.dp
import androidx.paging.PagingData
import androidx.paging.compose.collectAsLazyPagingItems
import io.github.murtazanasir.mailr.data.db.LocalDraftEntity
import io.github.murtazanasir.mailr.data.db.PendingOpEntity
import io.github.murtazanasir.mailr.data.db.PendingState
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.mail.ListActions
import io.github.murtazanasir.mailr.ui.mail.MailListPane
import io.github.murtazanasir.mailr.ui.mail.MailPlace
import io.github.murtazanasir.mailr.ui.mail.PendingBanner
import io.github.murtazanasir.mailr.ui.mail.PendingCallbacks
import io.github.murtazanasir.mailr.ui.mail.PendingChange
import io.github.murtazanasir.mailr.ui.mail.PendingChangesContent
import io.github.murtazanasir.mailr.ui.mail.SelectionBar
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.coroutines.flow.flowOf
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Spam, Trash and Pending changes on a phone, light and dark: the Spam and Trash actions (selection bar, list,
 * reader) and Pending changes (banner and sheet).
 * Output: app/screenshots/spamtrash-<theme>-<name>.png and pending-<theme>-<name>.png
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class SpamTrashScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()

    private val theme get() = if (dark) "dark" else "light"

    private fun shot(file: String, height: Int = 891, content: @Composable () -> Unit) {
        RuntimeEnvironment.setQualifiers("w411dp-h${height}dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) { content() }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/$file.png")
    }

    private val noList = ListActions({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> }, {}, { _, _ -> }, {}, {}, {})
    private val noConv = ConversationActions(back = {}, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
        openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {}, mailto = {}, retry = {})

    @Composable
    private fun List(view: MailView, selection: Set<Long> = emptySet(), waiting: Int = 0, failed: Int = 0) {
        val items = flowOf(PagingData.from(Fixtures.conversations.take(4))).collectAsLazyPagingItems()
        MailListPane(view, items, emptyList(), null, Fixtures.labels, selection, null, Prefs(),
            "Alex Morgan", "me@example.com", false, waiting, SnackbarHostState(), true, noList, pendingFailed = failed)
    }

    @Test fun spamSelection() = shot("spamtrash-$theme-spam-selection") {
        List(MailView("spam", title = "Spam"), selection = setOf(3075L, 3074L))
    }

    @Test fun trashSelection() = shot("spamtrash-$theme-trash-selection") {
        List(MailView("trash", title = "Trash"), selection = setOf(3066L))
    }

    @Test fun trashReader() = shot("spamtrash-$theme-trash-reader") {
        ConversationScreen(Fixtures.detail, null, Fixtures.labels, emptySet(), emptyMap(), null, null, false,
            SnackbarHostState(), noConv, place = MailPlace.TRASH)
    }

    @Test fun spamReader() = shot("spamtrash-$theme-spam-reader") {
        ConversationScreen(Fixtures.detail, null, Fixtures.labels, emptySet(), emptyMap(), null, null, false,
            SnackbarHostState(), noConv, place = MailPlace.SPAM)
    }

    @Test fun bars() = shot("spamtrash-$theme-bars", height = 260) {
        Column {
            SelectionBar(2, {}, {}, {}, {}, {}, anyUnread = true, place = MailPlace.SPAM)
            SelectionBar(1, {}, {}, {}, {}, {}, anyUnread = false, place = MailPlace.TRASH)
            SelectionBar(3, {}, {}, {}, {}, {}, anyUnread = true, place = MailPlace.OTHER)
        }
    }

    // ---------------- Pending changes ----------------

    private val changes = PendingChange.list(
        ops = listOf(
            PendingOpEntity(1, "k1", "POST", "conversations/actions", "{}", "Archive 2 conversations", 0, 1,
                "Server error. Try again.", PendingState.FAILED),
            PendingOpEntity(2, "k2", "POST", "conversations/actions", "{}", "Star 1 conversation", 0, 0),
            PendingOpEntity(3, "k3", "POST", "calendar/events", "{}", "Create event Budget review", 0, 2, "Too many requests. Try again shortly."),
        ),
        drafts = listOf(LocalDraftEntity("d1", payload = "{}", subject = "Re: Quarterly plan", updatedAt = 0)),
        paused = false,
    )

    private val noPending = PendingCallbacks({}, {}, {}, {}, {})

    @Test fun pendingSheet() = shot("pending-$theme-sheet") {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.BottomCenter) {
            Surface(shape = MaterialTheme.shapes.extraLarge, color = MaterialTheme.colorScheme.surfaceContainerLow,
                modifier = Modifier.fillMaxWidth().padding(top = 12.dp)) {
                PendingChangesContent(changes, paused = false, noPending)
            }
        }
    }

    @Test fun pendingSheetPaused() = shot("pending-$theme-sheet-paused", height = 520) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.BottomCenter) {
            Surface(shape = MaterialTheme.shapes.extraLarge, color = MaterialTheme.colorScheme.surfaceContainerLow,
                modifier = Modifier.fillMaxWidth()) {
                PendingChangesContent(changes.filter { !it.failed }.map { c ->
                    if (c.isDraft) c else c.copy(status = "Sign in again to sync") }, paused = true, noPending)
            }
        }
    }

    @Test fun pendingBanners() = shot("pending-$theme-banners", height = 200) {
        Column {
            PendingBanner(waiting = 3, failed = 0) {}
            PendingBanner(waiting = 2, failed = 1) {}
        }
    }

    @Test fun inboxWithFailedChanges() = shot("pending-$theme-list") {
        List(MailView(), waiting = 2, failed = 1)
    }
}
