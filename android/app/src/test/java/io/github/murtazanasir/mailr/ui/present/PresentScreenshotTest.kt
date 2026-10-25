package io.github.murtazanasir.mailr.ui.present

import android.app.Application
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.agent.AgentEvent
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.assistant.AssistantCallbacks
import io.github.murtazanasir.mailr.ui.assistant.AssistantScreen
import io.github.murtazanasir.mailr.ui.assistant.AssistantState
import io.github.murtazanasir.mailr.ui.assistant.ChatTimeline
import io.github.murtazanasir.mailr.ui.assistant.ChatTurn
import io.github.murtazanasir.mailr.ui.assistant.TurnItem
import io.github.murtazanasir.mailr.ui.more.ApprovalCard
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Screenshots of the presentation layer on a phone, light and dark: an approval card from the
 * fixtures (pending, and decided in place), and a reply with the "Categories & matching rules" table
 * as cards. Output: app/screenshots/present-<theme>-<name>.png. ACT approval.approve
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class PresentScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()

    @Before fun setUp() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
    }

    private fun resource(name: String) = javaClass.classLoader!!.getResource("design/$name")!!.readText()
    private val blocks = MailrJson.parseToJsonElement(resource("agent-blocks-fixtures.json")).jsonObject["approvals"]!!.jsonArray
    private fun fixture(name: String) = blocks.map { it.jsonObject }.first { it["name"]!!.jsonPrimitive.content == name }
    private fun display(name: String) = MailrJson.decodeFromJsonElement(Display.serializer(), fixture(name)["display"]!!)

    private fun shot(name: String, content: @Composable () -> Unit) {
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) { Surface(Modifier.fillMaxSize()) { content() } }
        }
        compose.onRoot().captureRoboImage("screenshots/present-${if (dark) "dark" else "light"}-$name.png")
    }

    private val noop = AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {})

    @Test fun approvalCards() = shot("approval-cards") {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            ApprovalCard(Approval(1, action = "rules.write", actor = "Ambrose", display = display("filter-create"))) { _, _, _ -> }
            ApprovalCard(Approval(2, action = "mail.trash", actor = "Ambrose", display = display("mail-trash"), status = "executed",
                outcome = Outcome("executed", "Approved · moved 6 messages to Trash"))) { _, _, _ -> }
        }
    }

    /** A card for an event change says which occurrence, before and after, and names calendars. */
    @Test fun eventCards() = shot("approval-event") {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            ApprovalCard(Approval(4, action = "calendar.update", actor = "Ambrose", display = display("event-update-occurrence"))) { _, _, _ -> }
            ApprovalCard(Approval(5, action = "calendar.update", actor = "Ambrose", display = display("event-move-calendar"))) { _, _, _ -> }
        }
    }

    @Test fun orderWithChoices() = shot("approval-order") {
        Column(Modifier.padding(16.dp)) {
            ApprovalCard(Approval(3, action = "orders.write", actor = "Ambrose", display = display("order-choices"))) { _, _, _ -> }
        }
    }

    @Test fun tableReply() {
        val md = MailrJson.parseToJsonElement(resource("agent-markdown-fixtures.json")).jsonObject["samples"]!!.jsonArray
            .map { it.jsonObject }.first { it["name"]!!.jsonPrimitive.content == "categories-table" }["normalized"]!!.jsonPrimitive.content
        shot("table-reply") {
            AssistantScreen(AssistantState(turns = listOf(ChatTurn("user", "Sort my mail into categories"),
                ChatTurn("assistant", items = listOf(TurnItem.Text(0, md, md))))), noop)
        }
    }

    @Test fun timelineWithResolvedCard() {
        var items = listOf<AgentEvent>(
            AgentEvent.Segment(0, "I will set up a filter for your finance mail.", emptyList()),
            AgentEvent.StepStarted("a", "manage_filter", "Creating a filter “Finance”", "label", 0),
            AgentEvent.StepDone("a", "manage_filter", "Creating a filter “Finance”", true, "waiting for your approval", null, 5, "label", 0),
            AgentEvent.ApprovalRequired(41, "Create a filter", emptyList(), 0),
            AgentEvent.Final("Approve the card above and the filter goes live.", emptyList(), 1),
        ).fold(emptyList<TurnItem>()) { acc, ev -> ChatTimeline.apply(acc, ev) }
        items = ChatTimeline.decided(items, 41, "executed")
        items = ChatTimeline.apply(items, AgentEvent.Session("s", null, true, 2))
        items = ChatTimeline.apply(items, AgentEvent.Final("Done. Mail from those six senders now gets the **Finance** label.", emptyList(), 2))
        val card = Approval(41, action = "rules.write", actor = "Ambrose", status = "executed", display = display("filter-create"),
            outcome = Outcome("executed", "Approved · created the filter"))
        shot("timeline") {
            AssistantScreen(AssistantState(turns = listOf(ChatTurn("user", "File my finance mail"),
                ChatTurn("assistant", items = items, approvals = listOf(card)))), noop)
        }
    }
}
