// ACT-SURFACES: android.list android.reader
package io.github.murtazanasir.mailr

import android.app.Application
import android.os.Looper
import androidx.compose.material3.SnackbarHostState
import androidx.compose.ui.test.assertCountEquals
import androidx.lifecycle.viewModelScope
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ConversationDetail
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.db.ListItemEntity
import io.github.murtazanasir.mailr.data.db.RemoteKeyEntity
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.SwipeAction
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.testing.settle
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.conversation.ConversationViewModel
import io.github.murtazanasir.mailr.ui.mail.MailPlace
import io.github.murtazanasir.mailr.ui.mail.MailboxViewModel
import io.github.murtazanasir.mailr.ui.mail.SelectionBar
import io.github.murtazanasir.mailr.ui.mail.swipeLabel
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The way out of Spam and Trash on the phone. Spam offers Not spam, Trash offers Restore
 * and Delete forever (confirmed, overlay-protected), and neither offers Report spam: on list
 * swipes, the selection bar and the reader.
 *
 * Interaction ids: mail.not_spam, mail.untrash, mail.delete_forever (android.list, android.reader)
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class SpamTrashTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private lateinit var server: MockWebServer
    private lateinit var g: AppGraph
    private val calls = CopyOnWriteArrayList<JsonObject>()

    private val spam = MailView("spam", title = "Spam")
    private val trash = MailView("trash", title = "Trash")

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                return when {
                    path == "/api/v1/conversations/actions" -> {
                        calls += MailrJson.parseToJsonElement(request.body.readUtf8()).jsonObject
                        MockResponse().setBody("""{"ok":true,"affected":1}""")
                    }
                    path.startsWith("/api/v1/labels") -> MockResponse().setBody(
                        MailrJson.encodeToString(ListSerializer(Label.serializer()), Fixtures.labels))
                    path == "/api/v1/conversations/3066" -> MockResponse().setBody(
                        MailrJson.encodeToString(ConversationDetail.serializer(), Fixtures.detail.copy(
                            conversation = Fixtures.detail.conversation.copy(unreadCount = 0, labelIds = listOf(5)))))
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        server.start()
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        SessionStore(ctx).apply {
            server = this@SpamTrashTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
        g = graphs.graph(ctx)
        runBlocking {
            for (v in listOf(spam, trash)) {
                val rows = Fixtures.conversations.take(3).mapIndexed { i, c ->
                    ListItemEntity(v.key, c.id, i.toLong(), MailrJson.encodeToString(ConversationSummary.serializer(), c))
                }
                g.db.lists().replacePage(v.key, rows, RemoteKeyEntity(v.key, null, rows.size, System.currentTimeMillis()))
            }
        }
    }

    @After fun tearDown() {
        idle()
        graphs.close()
        server.shutdown()
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idle()

    private fun call(action: String): JsonObject {
        eventually("POST conversations/actions $action") { calls.any { it["action"]!!.jsonPrimitive.content == action } }
        return calls.single { it["action"]!!.jsonPrimitive.content == action }
    }

    private fun JsonObject.ids() = this["ids"]!!.jsonArray.map { it.jsonPrimitive.long }
    private fun ids(v: MailView) = runBlocking { g.db.lists().first(v.key, 50).map { it.conversationId } }

    // ---------------- list ----------------

    /** The list's view model, after its first reads (labels) are done, so none outlives the test. */
    private fun mailbox(): MailboxViewModel = graphs.vm { MailboxViewModel(g) }.also {
        eventually("labels loaded") { g.mail.labels.value.isNotEmpty() }
    }

    @Test fun spamArchiveSwipeIsNotSpam() {
        val vm = mailbox()
        vm.open(spam)
        vm.swipe(Fixtures.conversations[0], SwipeAction.ARCHIVE) {}
        assertEquals(listOf(3075L), call("not_spam").ids())
        eventually("row leaves Spam") { 3075L !in ids(spam) }
    }

    @Test fun trashArchiveSwipeRestores() {
        val vm = mailbox()
        vm.open(trash)
        vm.swipe(Fixtures.conversations[1], SwipeAction.ARCHIVE) {}
        assertEquals(listOf(3066L), call("untrash").ids())
        eventually("row leaves Trash") { 3066L !in ids(trash) }
    }

    @Test fun trashDeleteSwipeAsksBeforeDeletingForever() {
        val vm = mailbox()
        vm.open(trash)
        // Whatever the swipe started has finished: a request it made has arrived by now.
        settle(vm.viewModelScope, g.scope, what = "the delete swipe") { vm.swipe(Fixtures.conversations[2], SwipeAction.TRASH) {} }
        assertEquals(listOf(3074L), vm.confirmDelete.value)
        assertTrue("nothing is sent before the answer", calls.isEmpty())
        vm.cancelDeleteForever()
        assertNull(vm.confirmDelete.value)
        // Asked again from the selection bar, and confirmed.
        vm.askDeleteForever(listOf(3074L, 3066L))
        vm.confirmDeleteForever()
        assertEquals(setOf(3074L, 3066L), call("delete_forever").ids().toSet())
        eventually("rows leave Trash") { 3074L !in ids(trash) && 3066L !in ids(trash) }
    }

    @Test fun trashSwipeInTheInboxStillTrashes() {
        val vm = mailbox()
        vm.swipe(Fixtures.conversations[0], SwipeAction.TRASH) {}
        assertEquals(listOf(3075L), call("trash").ids())
        assertNull(vm.confirmDelete.value)
    }

    @Test fun swipeLabelsFollowThePlace() {
        assertEquals("Restore", swipeLabel(MailPlace.TRASH, SwipeAction.ARCHIVE))
        assertEquals("Delete forever", swipeLabel(MailPlace.TRASH, SwipeAction.TRASH))
        assertEquals("Not spam", swipeLabel(MailPlace.SPAM, SwipeAction.ARCHIVE))
        assertNull(swipeLabel(MailPlace.SPAM, SwipeAction.TRASH))
        assertNull(swipeLabel(MailPlace.OTHER, SwipeAction.ARCHIVE))
    }

    @Test fun theReaderKnowsItsPlace() {
        val labels = Fixtures.labels
        assertEquals(MailPlace.TRASH, MailPlace.ofConversation(listOf(1), labels, "trash"))
        assertEquals(MailPlace.SPAM, MailPlace.ofConversation(listOf(4), labels, "inbox"))   // a notification or a search
        assertEquals(MailPlace.TRASH, MailPlace.ofConversation(listOf(5, 10), labels, "all"))
        assertEquals(MailPlace.OTHER, MailPlace.ofConversation(listOf(1, 10), labels, "inbox"))
        assertEquals(MailPlace.OTHER, MailPlace.ofConversation(null, labels, "starred"))
    }

    // ---------------- reader ----------------

    @Test fun readerDeleteForeverAndRestore() {
        val vm = graphs.vm { ConversationViewModel(g, 3066, null, null) }
        eventually("conversation loaded") { vm.detail.value != null }
        vm.act("untrash", closes = true)
        assertEquals(listOf(3066L), call("untrash").ids())
        vm.act("delete_forever", closes = true)
        assertEquals(listOf(3066L), call("delete_forever").ids())
    }

    // ---------------- menus ----------------

    private fun bar(place: MailPlace, acted: MutableList<String>) {
        compose.setContent {
            MailrTheme(ThemeMode.LIGHT, dynamic = false) {
                SelectionBar(2, {}, {}, { acted += it }, {}, {}, anyUnread = true, place = place)
            }
        }
    }

    @Test fun selectionBarInTrash() {
        val acted = mutableListOf<String>()
        bar(MailPlace.TRASH, acted)
        compose.onNodeWithContentDescription("Restore").performClick()
        compose.onNodeWithContentDescription("Delete forever").performClick()
        compose.onAllNodesWithContentDescriptionCount("Archive", 0)
        compose.onNodeWithContentDescription("More actions").performClick()
        compose.onAllNodesWithText("Report spam").assertCountEquals(0)
        compose.onAllNodesWithText("Snooze").assertCountEquals(0)
        assertEquals(listOf("untrash", "delete_forever"), acted)
    }

    @Test fun selectionBarInSpam() {
        val acted = mutableListOf<String>()
        bar(MailPlace.SPAM, acted)
        compose.onNodeWithContentDescription("Not spam").performClick()
        compose.onNodeWithContentDescription("More actions").performClick()
        compose.onAllNodesWithText("Report spam").assertCountEquals(0)
        assertEquals(listOf("not_spam"), acted)
    }

    @Test fun selectionBarElsewhereStillReportsSpam() {
        val acted = mutableListOf<String>()
        bar(MailPlace.OTHER, acted)
        compose.onNodeWithContentDescription("More actions").performClick()
        // The ⋮ sheet scrolls on a small screen; Report spam is its last row.
        compose.onNodeWithText("Report spam").performScrollTo().performClick()
        assertEquals(listOf("spam"), acted)
    }

    private fun reader(place: MailPlace, acted: MutableList<String>) {
        val actions = ConversationActions(back = {}, act = { acted += it }, toggle = {}, expandAll = {}, star = { _, _ -> },
            reply = { _, _ -> }, openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {},
            unsubscribe = {}, mailto = {}, retry = {})
        compose.setContent {
            MailrTheme(ThemeMode.LIGHT, dynamic = false) {
                ConversationScreen(Fixtures.detail, null, Fixtures.labels, emptySet(), emptyMap(), null, null, false,
                    SnackbarHostState(), actions, place = place)
            }
        }
    }

    @Test fun readerInTrashConfirmsDeleteForever() {
        val acted = mutableListOf<String>()
        reader(MailPlace.TRASH, acted)
        compose.onNodeWithContentDescription("Delete forever").performClick()
        assertTrue(acted.isEmpty())
        compose.onNodeWithText("Delete forever?").assertExistsCompat()
        compose.onNodeWithTag("delete-forever-confirm").performClick()
        compose.onNodeWithContentDescription("Restore").performClick()
        compose.onNodeWithContentDescription("More").performClick()
        compose.onAllNodesWithText("Report spam").assertCountEquals(0)
        assertEquals(listOf("delete_forever", "untrash"), acted)
    }

    @Test fun readerInSpamOffersNotSpam() {
        val acted = mutableListOf<String>()
        reader(MailPlace.SPAM, acted)
        compose.onNodeWithContentDescription("Not spam").performClick()
        compose.onNodeWithContentDescription("More").performClick()
        compose.onAllNodesWithText("Report spam").assertCountEquals(0)
        assertEquals(listOf("not_spam"), acted)
    }

    private fun androidx.compose.ui.test.SemanticsNodeInteraction.assertExistsCompat() = assertExists()

    private fun androidx.compose.ui.test.junit4.ComposeContentTestRule.onAllNodesWithContentDescriptionCount(d: String, n: Int) =
        onAllNodes(androidx.compose.ui.test.hasContentDescription(d)).assertCountEquals(n)
}
