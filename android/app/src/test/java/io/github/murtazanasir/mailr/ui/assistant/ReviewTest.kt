// ACT-SURFACES: android.assistant
package io.github.murtazanasir.mailr.ui.assistant

import android.app.Application
import android.os.Looper
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertContentDescriptionEquals
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.ReviewLabel
import io.github.murtazanasir.mailr.data.api.ReviewState
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.more.ApprovalCard
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.Collections

/**
 * A finished review's card (agent/review.py `card`): two moves, set-aside reasons, kept and unsure rows; one
 * sender with its address and a suggested filter ("Always …"). Demo names only.
 */
internal const val REVIEW_CARD = """{"id":900,"actor":"Ambrose","action":"mail.trash","status":"pending",
 "preview":{"title":"Make 9 changes from the review of Promotions","kind":"review","lines":["Move to Trash: 7 (Lumen Coffee 4, Northwind Deals 3)"],
  "review":{"review_id":5,"scope":"Promotions","total":14,"model":"local",
   "groups":[
    {"move":"trash","count":7,"senders":[
      {"from":"Lumen Coffee","count":4,"conversation_ids":[11,12,13,14],"subjects":["Autumn blend is here","Last call: 20% off","Your loyalty points"],
       "email":"news@lumen.example","suggest_filter":true},
      {"from":"Northwind Deals","count":3,"conversation_ids":[21,22,23],"subjects":["Weekend sale","New arrivals"]}]},
    {"move":"label","label":"Receipts","label_id":7,"count":2,"senders":[
      {"from":"Tailwind Rail","count":2,"conversation_ids":[31,32],"subjects":["Your ticket to Chicago"]}]}],
   "set_aside":{"starred":2,"invitation":1},
   "kept":[{"conversation_id":41,"from":"Riverside Library","subject":"Your holds are ready","reason":"pickup notice"}],"kept_count":1,
   "unsure":[{"conversation_id":51,"from":"Harbor Gym","subject":"Membership update","reason":"may need action"},
             {"conversation_id":52,"from":"Harbor Gym","subject":"Class schedule","reason":""}],"unsure_count":3,
   "labels":[{"id":8,"name":"Travel"}]}}}"""

/**
 * agent.review_mail in the Ambrose chat, as on the web (ReviewProgress.tsx, ReviewPlan.tsx, ApprovalCard.tsx):
 * the `review` event and a reopened chat's `reviews` put a progress line on the turn; it is read every 2 s
 * while the review runs, says how far it got, with Stop and Continue; the finished review's card joins the
 * turn once; a move menu per sender, conversation and kept or unsure row regroups the card, and Apply
 * sends the user's moves as `changes` (agent.review_move).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ReviewTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private val app: Application = ApplicationProvider.getApplicationContext()
    private var server: MockWebServer? = null
    private val requests: MutableList<String> = Collections.synchronizedList(mutableListOf())
    /** The answers GET /agent/reviews/5 gives, one per read (the last repeats). */
    private val progress: MutableList<String> = Collections.synchronizedList(mutableListOf())

    @After fun tearDown() {
        AssistantViewModel.REVIEW_POLL_MS = 2000L
        AssistantViewModel.REVIEW_RETRY_MS = 5000L
        shadowOf(Looper.getMainLooper()).idle()
        graphs.close()
        server?.shutdown()
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idleFor(java.time.Duration.ofMillis(10))
    private fun until(what: String, cond: () -> Boolean) = eventually(what, idle = ::idle, detail = { "requests=$requests" }, check = cond)

    private fun state(status: String, done: Int, approval: Long? = null) =
        """{"review_id":5,"status":"$status","scope":"Promotions","total":14,"done":$done,"set_aside":3,"decided":{},"approval_id":${approval ?: "null"},"error":null}"""

    private val session = """{"id":"c7","title":"Clean up promotions","turns":[
     {"role":"user","id":3,"content":"/sweep","citations":[],"steps":[]},
     {"role":"assistant","content":"I started a review of Promotions.","status":"done","citations":[],"steps":[]}],
     "running":false,"pending_approvals":[],"reviews":[{"review_id":5}]}"""

    private fun model(): AssistantViewModel {
        AssistantViewModel.REVIEW_POLL_MS = 10L
        AssistantViewModel.REVIEW_RETRY_MS = 10L
        val s = MockWebServer()
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = request.body.readUtf8()
                requests += "${request.method} ${request.path} $body".trim()
                return when {
                    request.path == "/api/v1/agent/sessions/c7" -> MockResponse().setBody(session)
                    request.method == "POST" && request.path == "/api/v1/agent/chat" -> MockResponse().setHeader("Content-Type", "text/event-stream")
                        .setBody(listOf(
                            "session" to """{"session_id":"c7","title":"Clean up promotions","seq":1}""",
                            "review" to """{"review_id":5,"seq":2}""",
                            "final" to """{"content":"I started a review of Promotions.","citations":[],"segment":0,"seq":3}""",
                            "done" to """{"stopped":false,"seq":4}""").joinToString("") { (t, d) -> "event: $t\ndata: $d\n\n" })
                    request.path == "/api/v1/agent/reviews/5" -> MockResponse().setBody(
                        synchronized(progress) { if (progress.size > 1) progress.removeAt(0) else progress.firstOrNull() ?: state("running", 0) })
                    request.path == "/api/v1/agent/reviews/5/stop" -> MockResponse().setBody(state("stopping", 50))
                    request.path == "/api/v1/agent/reviews/5/resume" -> MockResponse().setBody(state("running", 50))
                    request.path == "/api/v1/approvals/900" -> MockResponse().setBody(REVIEW_CARD)
                    request.path == "/api/v1/approvals/900/decide" -> MockResponse().setBody("""{"status":"approved"}""")
                    request.method == "POST" && request.path == "/api/v1/rules" ->
                        if (rulesFail) MockResponse().setResponseCode(500).setBody("""{"detail":"boom"}""") else MockResponse().setBody("""{"id":31}""")
                    request.method == "DELETE" && request.path == "/api/v1/rules/31" -> MockResponse().setBody("{}")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        s.start()
        server = s
        SessionStore(app).apply { server = s.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        val g = graphs.graph(app)
        return graphs.vm { AssistantViewModel(g, SavedStateHandle()) }
    }

    private fun reads() = requests.count { it == "GET /api/v1/agent/reviews/5" }
    /** POST /rules fails (500). */
    @Volatile private var rulesFail = false

    @Test fun theReviewCardParses() {
        val a = MailrJson.decodeFromString(Approval.serializer(), REVIEW_CARD)
        val r = a.review!!
        assertEquals(5L, r.reviewId)
        assertEquals("Promotions", r.scope)
        assertEquals(listOf("trash", "label"), r.groups.map { it.move })
        assertEquals("Receipts", r.groups[1].label)
        assertEquals(listOf(11L, 12L, 13L, 14L), r.groups[0].senders[0].conversationIds)
        assertEquals(3, r.groups[0].senders[0].subjects.size)
        assertEquals(mapOf("starred" to 2, "invitation" to 1), r.setAside)
        assertEquals(1, r.keptCount)
        assertEquals(3, r.unsureCount)
        assertEquals("may need action", r.unsure[0].reason)
        // Another card, or a review plan without kind "review", is an ordinary card.
        val plain = MailrJson.decodeFromString(Approval.serializer(), REVIEW_CARD.replace("\"kind\":\"review\"", "\"kind\":\"other\""))
        assertNull(plain.review)
        assertNull(MailrJson.decodeFromString(Approval.serializer(), """{"id":1,"preview":{"title":"Trash 2"}}""").review)
    }

    @Test fun theProgressParses() {
        val r = MailrJson.decodeFromString(ReviewState.serializer(), state("stopping", 50, 900))
        assertEquals("stopping", r.status)
        assertEquals(50, r.done)
        assertEquals(3, r.setAside)
        assertEquals(900L, r.approvalId)
        assertTrue(r.live)
        assertTrue(!r.copy(status = "stopped").live)
    }

    @Test fun applySendsTheChangesByConversation() {
        assertEquals("""{"decision":"approve","scope":"once","changes":{"21":"keep","31":"trash","11":"label:8"}}""",
            MailrApi.decideBody(true, "once", changes = mapOf(21L to "keep", 31L to "trash", 11L to "label:8")).toString())
        assertEquals("""{"decision":"approve","scope":"once"}""", MailrApi.decideBody(true, "once").toString())
        // A cleanup plan's except_ids still go as they were.
        assertEquals("""{"decision":"approve","scope":"once","except_ids":[21,22]}""",
            MailrApi.decideBody(true, "once", listOf(21L, 22L, 21L)).toString())
        // Discard never sends changes.
        assertEquals("""{"decision":"deny","scope":"once"}""", MailrApi.decideBody(false, "once", changes = mapOf(21L to "keep")).toString())
    }

    // The move menu's changes (the web's ReviewPlan.tsx onMove), without the UI.

    private val items get() = reviewItems(MailrJson.decodeFromString(Approval.serializer(), REVIEW_CARD).review!!)

    @Test fun everyConversationOnTheCardHasTheReviewsMove() {
        val byId = items.associate { it.id to it.orig }
        assertEquals("trash", byId[11]); assertEquals("trash", byId[23])
        assertEquals("label:7", byId[31])
        assertEquals("keep", byId[41])
        assertEquals("unsure", byId[51])
        // A card from before the per-conversation list: its sample subjects, blank past them.
        assertEquals(listOf("Autumn blend is here", "Last call: 20% off", "Your loyalty points", ""), items.filter { it.from == "Lumen Coffee" }.map { it.subject })
        // The labels the menu offers: the review's own, then the one its group uses.
        val labels = reviewLabels(MailrJson.decodeFromString(Approval.serializer(), REVIEW_CARD).review!!)
        assertEquals(listOf(ReviewLabel(8, "Travel"), ReviewLabel(7, "Receipts")), labels)
        assertEquals(listOf("trash", "archive", "read", "label:8", "label:7", "keep"), moveKeys(labels))
    }

    @Test fun theChangesHoldOnlyWhatDiffersFromTheReview() {
        // A sender to Keep: each of its conversations.
        var c = withMove(items, emptyMap(), listOf(21L, 22L, 23L), "keep")
        assertEquals(mapOf(21L to "keep", 22L to "keep", 23L to "keep"), c)
        // One conversation to a label, a kept row to Trash, an unsure row to Archive.
        c = withMove(items, c, listOf(11L), "label:8")
        c = withMove(items, c, listOf(41L), "trash")
        c = withMove(items, c, listOf(51L), "archive")
        assertEquals(mapOf(21L to "keep", 22L to "keep", 23L to "keep", 11L to "label:8", 41L to "trash", 51L to "archive"), c)
        assertEquals(6, c.size)
        // Back to the review's own move: no change any more; an id not on the card is never added.
        c = withMove(items, c, listOf(22L), "trash")
        c = withMove(items, c, listOf(41L), "keep")
        c = withMove(items, c, listOf(999L), "trash")
        assertEquals(mapOf(21L to "keep", 23L to "keep", 11L to "label:8", 51L to "archive"), c)
    }

    @Test fun aChangedRowMovesToItsNewGroup() {
        val labels = reviewLabels(MailrJson.decodeFromString(Approval.serializer(), REVIEW_CARD).review!!)
        val l = reviewLayout(items, labels, mapOf(21L to "archive", 22L to "archive", 23L to "archive", 11L to "label:8", 31L to "keep", 51L to "trash"))
        assertEquals(listOf("trash", "archive", "label:8", "label:7"), l.groups.map { it.first })
        val trash = l.groups[0].second
        assertEquals(listOf("Lumen Coffee" to 3, "Harbor Gym" to 1), trash.map { it.first to it.second.size })
        assertEquals(listOf("Northwind Deals"), l.groups[1].second.map { it.first })
        assertEquals(listOf(11L), l.groups[2].second.single().second.map { it.id })
        assertEquals(listOf(32L), l.groups[3].second.single().second.map { it.id })
        // Kept by the review, kept by the user, and still unsure.
        assertEquals(listOf(41L), l.keptModel.map { it.id })
        assertEquals(listOf(31L), l.keptByYou.map { it.id })
        assertEquals(listOf(52L), l.unsure.map { it.id })
    }

    @Test fun theReviewEventPutsTheProgressOnTheTurnAndTheCardJoinsItOnce() {
        val vm = model()
        vm.send("/sweep")
        until("answered") { !vm.state.value.busy && vm.state.value.sessionId == "c7" }
        assertEquals(listOf(5L), vm.state.value.turns.last().reviews)

        progress += listOf(state("running", 25), state("running", 50), state("done", 14, 900))
        vm.viewModelScope.launch { vm.followReview(5) }
        until("the card arrives") { vm.state.value.turns.last().approvals.any { it.id == 900L } }
        assertEquals("done", vm.state.value.reviews[5]?.status)
        assertEquals(3, reads())
        // Polling ended with the review: no more reads.
        repeat(20) { idle() }
        assertEquals(3, reads())

        // Read again (the line came back on screen): the card is not added twice.
        vm.viewModelScope.launch { vm.followReview(5) }
        until("read again") { reads() == 4 && requests.count { it == "GET /api/v1/approvals/900" } == 2 }
        repeat(5) { idle() }
        assertEquals(1, vm.state.value.turns.sumOf { t -> t.approvals.count { it.id == 900L } })
    }

    @Test fun aReopenedChatFollowsItsReviewsAndKeepsACardItHolds() {
        val vm = model()
        vm.open("c7")
        until("opened") { vm.state.value.sessionId == "c7" && vm.state.value.turns.size == 2 }
        assertEquals(listOf(5L), vm.state.value.turns[1].reviews)
        // The card is already in the chat (pending_approvals): it changes in place, it is not added again.
        val card = MailrJson.decodeFromString(Approval.serializer(), REVIEW_CARD)
        vm.addReviewCard(5, card)
        vm.addReviewCard(5, card.copy(status = "executed"))
        assertEquals(1, vm.state.value.turns[1].approvals.size)
        assertEquals("executed", vm.state.value.turns[1].approvals[0].status)
    }

    @Test fun stopAndContinue() {
        val vm = model()
        vm.reviewAction(5, stop = true)
        until("stopping") { vm.state.value.reviews[5]?.status == "stopping" && vm.state.value.reviewActing.isEmpty() }
        vm.reviewAction(5, stop = false)
        until("running again") { vm.state.value.reviews[5]?.status == "running" }
        assertTrue(requests.any { it.startsWith("POST /api/v1/agent/reviews/5/stop") })
        assertTrue(requests.any { it.startsWith("POST /api/v1/agent/reviews/5/resume") })
    }

    @Test fun applySendsTheChangesFromTheChat() {
        val vm = model()
        val card = MailrJson.decodeFromString(Approval.serializer(), REVIEW_CARD)
        vm.applyReview(card, mapOf(21L to "keep", 31L to "archive", 11L to "label:8"))
        until("decided") { requests.any { it.startsWith("POST /api/v1/approvals/900/decide") } }
        val sent = requests.first { it.startsWith("POST /api/v1/approvals/900/decide") }.substringAfter("decide ")
        val body = MailrJson.parseToJsonElement(sent) as JsonObject
        assertEquals("approve", body["decision"].toString().trim('"'))
        assertEquals("once", body["scope"].toString().trim('"'))
        assertEquals("""{"21":"keep","31":"archive","11":"label:8"}""", body["changes"].toString())
        assertNull(body["except_ids"])
    }

    // The progress line, one state at a time.

    private fun line(r: ReviewState, onStop: () -> Unit = {}, onResume: () -> Unit = {}) = compose.setContent {
        MaterialTheme { Surface(Modifier.fillMaxSize()) { ReviewProgressLine(r, onStop = onStop, onResume = onResume) } }
    }

    private val base = ReviewState(5, "running", "Promotions", total = 143, done = 50, setAside = 9)

    @Test fun runningSaysHowFarWithStop() {
        var stops = 0
        line(base, onStop = { stops++ })
        compose.onNodeWithText("Reviewing Promotions: 143 conversations").assertExists()
        compose.onNodeWithText("50 of 143 checked · 9 kept without asking", substring = true).assertExists()
        compose.onNodeWithTag("review-stop").performClick()
        assertEquals(1, stops)
        compose.onNodeWithTag("review-continue").assertDoesNotExist()
    }

    @Test fun stoppingHasNoButtons() {
        line(base.copy(status = "stopping"))
        compose.onNodeWithText("Stopping after this batch…").assertExists()
        compose.onNodeWithTag("review-stop").assertDoesNotExist()
        compose.onNodeWithTag("review-continue").assertDoesNotExist()
    }

    @Test fun stoppedOffersContinue() {
        var resumes = 0
        line(base.copy(status = "stopped"), onResume = { resumes++ })
        compose.onNodeWithText("Review stopped at 50 of 143").assertExists()
        compose.onNodeWithTag("review-continue").performClick()
        assertEquals(1, resumes)
    }

    @Test fun failedSaysWhyWithContinue() {
        line(base.copy(status = "failed", error = "the model is not answering"))
        compose.onNodeWithText("The review stopped: the model is not answering").assertExists()
        compose.onNodeWithTag("review-continue").assertExists()
    }

    @Test fun readyAndNothingToChange() {
        line(base.copy(status = "done", done = 143, approvalId = 900))
        compose.onNodeWithText("Review of 143 conversations is ready: check the card below.").assertExists()
        compose.onNodeWithTag("review-stop").assertDoesNotExist()
    }

    @Test fun nothingToChangeOne() {
        line(base.copy(status = "done", total = 1, done = 1))
        compose.onNodeWithText("Reviewed 1 conversation: nothing to change.").assertExists()
    }

    // The review card.

    @Test fun aMoveMenuRegroupsTheSenderAndApplySendsTheChanges() {
        val card = MailrJson.decodeFromString(Approval.serializer(), REVIEW_CARD)
        val applied = mutableListOf<Map<Long, String>>()
        val decided = mutableListOf<String>()
        compose.setContent {
            MaterialTheme { Surface(Modifier.fillMaxSize()) {
                ApprovalCard(card, onApplyReview = { _, ids -> applied += ids }) { _, ok, scope -> decided += "$ok/$scope" }
            } }
        }
        // Each heading, and the move menu of each of its senders.
        compose.onAllNodesWithText("Move to Trash").assertCountEquals(3)
        compose.onAllNodesWithText("Label “Receipts”").assertCountEquals(2)
        compose.onNodeWithText("Kept: 2 starred, 1 invitation, 1 still useful").assertExists()
        compose.onNodeWithText("Autumn blend is here · Last call: 20% off · Your loyalty points").assertExists()
        assertEquals(3, compose.onAllNodesWithTag("review-sender").fetchSemanticsNodes().size)
        // Only Apply and Discard: no grants.
        compose.onNodeWithTag("approval-grant-session").assertDoesNotExist()
        compose.onNodeWithText("Apply").assertExists()

        compose.onNodeWithTag("review-changed").assertDoesNotExist()

        // Northwind Deals (the second sender's menu) to Archive: an Archive group with its 3, Trash keeps 4.
        compose.onAllNodesWithTag("review-move")[1].assertIsEnabled().assertContentDescriptionEquals("What happens to Northwind Deals")
        compose.onAllNodesWithTag("review-move")[1].performClick()
        compose.onNodeWithTag("review-move-archive").performClick()
        compose.onAllNodesWithText("Archive").assertCountEquals(2)
        compose.onAllNodesWithText("Move to Trash").assertCountEquals(2)
        compose.onNodeWithText("3 changed by you").assertExists()
        compose.onNodeWithText("Apply").performClick()
        assertEquals(mapOf(21L to "archive", 22L to "archive", 23L to "archive"), applied[0])

        // One of Lumen Coffee's conversations (behind its expander) to Keep: "1 kept: you chose Keep" opens with it.
        compose.onAllNodesWithTag("review-expand")[0].assertContentDescriptionEquals("Conversations from Lumen Coffee").performClick()
        assertEquals(4, compose.onAllNodesWithTag("review-conversation").fetchSemanticsNodes().size)
        compose.onAllNodesWithTag("review-move")[1].performClick()
        compose.onNodeWithTag("review-move-keep").performClick()
        compose.onNodeWithText("1 kept: you chose Keep").assertExists()
        compose.onNodeWithText("Kept: 2 starred, 1 invitation, 1 still useful, 1 you chose to keep").assertExists()
        compose.onNodeWithText("4 changed by you").assertExists()

        // Northwind Deals back to Move to Trash: no longer a change.
        compose.onNode(hasTestTag("review-move") and hasContentDescription("What happens to Northwind Deals")).performClick()
        compose.onNodeWithTag("review-move-trash").performClick()
        compose.onNodeWithText("1 changed by you").assertExists()
        compose.onNodeWithText("Apply").performClick()
        assertEquals(mapOf(11L to "keep"), applied[1])

        compose.onNodeWithTag("review-discard").performClick()
        assertEquals(listOf("false/once"), decided)
    }

    @Test fun keptAndUnsureRowsOpen() {
        val card = MailrJson.decodeFromString(Approval.serializer(), REVIEW_CARD)
        compose.setContent { MaterialTheme { Surface(Modifier.fillMaxSize()) { ApprovalCard(card) { _, _, _ -> } } } }
        compose.onNodeWithText("Membership update", substring = true).assertDoesNotExist()
        compose.onNodeWithText("3 left alone: not sure").performClick()
        compose.onNodeWithText("Membership update", substring = true).assertExists()
        compose.onNodeWithText("and 1 more").assertExists()
        compose.onNodeWithText("1 kept: still useful").performClick()
        compose.onNodeWithText("Your holds are ready", substring = true).assertExists()
        // The kept and unsure rows have a move menu each.
        assertEquals(3, compose.onAllNodesWithTag("review-row").fetchSemanticsNodes().size)
        // Without a way to apply changes, the menus cannot change.
        compose.onAllNodesWithTag("review-move")[0].assertIsNotEnabled()
    }

    @Test fun aDecidedReviewCardFoldsItsPlanBehindDetails() {
        val card = MailrJson.decodeFromString(Approval.serializer(), REVIEW_CARD).copy(status = "executed")
        compose.setContent { MaterialTheme { Surface(Modifier.fillMaxSize()) { ApprovalCard(card) { _, _, _ -> } } } }
        compose.onNodeWithTag("review-plan").assertDoesNotExist()
        compose.onNodeWithTag("review-apply").assertDoesNotExist()
        compose.onNodeWithText("Details").performClick()
        compose.onNodeWithTag("review-plan").assertExists()
    }

    // "Always …" (agent.review_filter): a filter for a sender's future mail, as the web's AlwaysButton and filterFor.

    private val lumen = io.github.murtazanasir.mailr.data.api.ReviewSender("Lumen Coffee", 4, listOf(11L, 12L, 13L, 14L),
        email = "news@lumen.example", suggestFilter = true)
    private fun group(move: String, label: String? = null, labelId: Long? = null) =
        io.github.murtazanasir.mailr.data.api.ReviewGroup(move, label, labelId, 4, listOf(lumen))

    @Test fun theFilterDoesWhatTheReviewDoesForEachMove() {
        fun body(g: io.github.murtazanasir.mailr.data.api.ReviewGroup) = filterFor(g, lumen)?.toString()
        assertEquals("""{"name":"Lumen Coffee: Move to Trash","query":"from:news@lumen.example","actions":{"trash":true},"enabled":true}""", body(group("trash")))
        assertEquals("""{"name":"Lumen Coffee: Archive","query":"from:news@lumen.example","actions":{"archive":true},"enabled":true}""", body(group("archive")))
        assertEquals("""{"name":"Lumen Coffee: Mark read","query":"from:news@lumen.example","actions":{"mark_read":true},"enabled":true}""", body(group("read")))
        assertEquals("""{"name":"Lumen Coffee: Label “Receipts”","query":"from:news@lumen.example","actions":{"add_labels":[7]},"enabled":true}""",
            body(group("label", "Receipts", 7)))
        // No label id, or no address: no filter.
        assertNull(body(group("label", "Receipts")))
        assertNull(filterFor(group("trash"), lumen.copy(email = null)))
    }

    @Test fun alwaysMakesTheFilterAndUndoDeletesIt() {
        val vm = model()
        val seen = Collections.synchronizedList(mutableListOf<ChatsMessage>())
        graphs.scope().launch { vm.renames.collect { seen += it } }
        val key = alwaysKey(5, group("trash"), lumen)
        vm.makeReviewFilter(5, group("trash"), lumen)
        until("made") { key in vm.state.value.filtersMade && seen.isNotEmpty() }
        assertEquals("""POST /api/v1/rules {"name":"Lumen Coffee: Move to Trash","query":"from:news@lumen.example","actions":{"trash":true},"enabled":true}""",
            requests.single { it.startsWith("POST /api/v1/rules") })
        assertEquals("Filter made for future mail from Lumen Coffee", seen[0].text)
        assertEquals(MadeFilter(key, 31), seen[0].undoFilter)
        assertTrue(vm.state.value.filtersBusy.isEmpty())
        // A second tap makes no second filter.
        vm.makeReviewFilter(5, group("trash"), lumen)
        idle()
        assertEquals(1, requests.count { it.startsWith("POST /api/v1/rules") })

        vm.undoFilter(seen[0].undoFilter!!)
        until("undone") { key !in vm.state.value.filtersMade }
        assertTrue(requests.contains("DELETE /api/v1/rules/31 {}") || requests.contains("DELETE /api/v1/rules/31"))
    }

    @Test fun alwaysSendsTheLabelsIdForALabelMove() {
        val vm = model()
        vm.makeReviewFilter(5, group("label", "Receipts", 7), lumen)
        until("made") { vm.state.value.filtersMade.isNotEmpty() }
        assertTrue(requests.single { it.startsWith("POST /api/v1/rules") }.endsWith(""""actions":{"add_labels":[7]},"enabled":true}"""))
    }

    @Test fun aFilterThatCannotBeMadeSaysSo() {
        val vm = model()
        rulesFail = true
        val notices = Collections.synchronizedList(mutableListOf<String>())
        graphs.scope().launch { vm.notices.collect { notices += it } }
        vm.makeReviewFilter(5, group("archive"), lumen)
        until("failed") { notices.isNotEmpty() }
        assertEquals("Could not make the filter", notices[0])
        assertTrue(vm.state.value.filtersMade.isEmpty())
        assertTrue(vm.state.value.filtersBusy.isEmpty())
    }

    @Test fun theCardShowsAlwaysUnderASenderWithASuggestedFilter() {
        val card = MailrJson.decodeFromString(Approval.serializer(), REVIEW_CARD)
        val made = androidx.compose.runtime.mutableStateOf(emptySet<String>())
        val taps = mutableListOf<String>()
        compose.setContent {
            MaterialTheme { Surface(Modifier.fillMaxSize()) {
                ApprovalCard(card, onApplyReview = { _, _ -> }, reviewAlways = ReviewAlways(made.value, emptySet()) { rid, g, s ->
                    taps += alwaysKey(rid, g, s) }) { _, _, _ -> }
            } }
        }
        // Only Lumen Coffee has one: Northwind Deals and Tailwind Rail carry no suggestion.
        assertEquals(1, compose.onAllNodesWithTag("review-always").fetchSemanticsNodes().size)
        compose.onNodeWithText("Always move to Trash").performClick()
        assertEquals(listOf("5/trash//Lumen Coffee"), taps)
        made.value = setOf("5/trash//Lumen Coffee")
        compose.waitForIdle()
        compose.onNodeWithTag("review-always").assertDoesNotExist()
        compose.onNodeWithText("Filter made").assertExists()
    }

    @Test fun withoutTheChatTheCardHasNoAlways() {
        val card = MailrJson.decodeFromString(Approval.serializer(), REVIEW_CARD)
        compose.setContent { MaterialTheme { Surface(Modifier.fillMaxSize()) { ApprovalCard(card) { _, _, _ -> } } } }
        compose.onNodeWithTag("review-always").assertDoesNotExist()
    }
}
