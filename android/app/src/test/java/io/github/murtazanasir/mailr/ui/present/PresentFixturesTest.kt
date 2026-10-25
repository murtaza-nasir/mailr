package io.github.murtazanasir.mailr.ui.present

import android.app.Application
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.requiredWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.ui.more.ApprovalCard
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The Android renderers on the golden fixtures the web also runs (spec/fixtures/agent-blocks-fixtures.json
 * and agent-markdown-fixtures.json, copies in test resources; produced by backend/mailr/agent/present.py).
 * ACT approval.approve, ACT approval.deny
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class PresentFixturesTest {
    @get:Rule val compose = createComposeRule()

    private fun resource(name: String) = javaClass.classLoader!!.getResource("design/$name")!!.readText()
    private val blocks = MailrJson.parseToJsonElement(resource("agent-blocks-fixtures.json")).jsonObject
    private val markdown = MailrJson.parseToJsonElement(resource("agent-markdown-fixtures.json")).jsonObject
    private val approvals = blocks["approvals"]!!.jsonArray.map { it.jsonObject }

    private fun display(f: JsonObject) = MailrJson.decodeFromJsonElement(Display.serializer(), f["display"]!!)
    private fun outcomes(f: JsonObject) = MailrJson.decodeFromJsonElement(ListSerializer(Outcome.serializer()), f["outcomes"]!!)
    private fun expect(f: JsonObject) = f["expect"]!!.jsonArray.map { it.jsonPrimitive.content }

    private fun shows(s: String) = compose.onAllNodesWithText(s, substring = true, useUnmergedTree = true)
        .fetchSemanticsNodes().isNotEmpty()

    @Test fun everyApprovalFixtureShowsItsWords() {
        assertEquals(1, blocks["version"]!!.jsonPrimitive.int)
        assertTrue(approvals.size >= 10)
        var card by mutableStateOf<Approval?>(null)
        compose.setContent { MaterialTheme { card?.let { ApprovalCard(it) { _, _, _ -> } } } }
        approvals.forEach { f ->
            val d = display(f)
            card = Approval(id = 1, action = f["action"]!!.jsonPrimitive.content, actor = "Ambrose", display = d)
            compose.waitForIdle()
            expect(f).forEach { s -> assertTrue("${f["name"]}: “$s”", shows(s.replace(Regex("\\s+"), " ").trim())) }
            assertTrue("${f["name"]}: raw machine text", !shows("add_labels=") && !shows("Do: "))
        }
    }

    @Test fun decidedCardsShowTheirOutcomeInPlace() {
        var card by mutableStateOf<Approval?>(null)
        compose.setContent { MaterialTheme { card?.let { ApprovalCard(it) { _, _, _ -> } } } }
        approvals.forEach { f ->
            outcomes(f).filter { it.state != "pending" }.forEach { o ->
                card = Approval(id = 1, action = f["action"]!!.jsonPrimitive.content, display = display(f), status = o.state, outcome = o)
                compose.waitForIdle()
                assertTrue("${f["name"]}/${o.state}", shows(o.text))
                assertTrue("buttons are gone once decided", !shows("Allow once"))
            }
        }
    }

    @Test fun anUnknownValueTypeShowsItsText() {
        val d = Display(title = "T", fields = listOf(DisplayField("Future", DisplayValue(t = "hologram", text = "shown as text"))))
        compose.setContent { MaterialTheme { ApprovalBody(d) } }
        assertTrue(shows("shown as text"))
    }

    /**
     * agent.match_check: the match check on a filter card
     * (agent/present.py _match_field) is a "Matches now" row of the display, in the app's language.
     */
    @Test fun theMatchCheckShowsOnAFilterCard() {
        val d = MailrJson.decodeFromString(Display.serializer(), """{"v":1,"icon":"filter","title":"Create a filter","fields":[
            {"label":"Matches now","label_msg":{"key":"approvals.field.matches_now","params":{}},
             "value":{"t":"text","text":"3 emails in the last 6 months","msg":{"key":"approvals.match.some",
              "params":{"count":3,"subject":"Weekend sale","date":"14 Oct"}}}},
            {"label":"Matches now","label_msg":{"key":"approvals.field.matches_now","params":{}},
             "value":{"t":"text","text":"No email","msg":{"key":"approvals.match.none","params":{}}}}]}""")
        compose.setContent { MaterialTheme { ApprovalCard(Approval(id = 1, action = "rules.write", display = d)) { _, _, _ -> } } }
        assertTrue(shows("Matches now"))
        assertTrue(shows("3 emails in the last 6 months, the newest “Weekend sale” on 14 Oct"))
        assertTrue(shows("No email in the last 6 months. Check the search before you approve."))
    }

    @Test fun markdownFixturesSplitAndLayOutAsExpected() {
        val samples = markdown["samples"]!!.jsonArray.map { it.jsonObject }
        assertTrue(samples.any { it["name"]!!.jsonPrimitive.content == "categories-table" })
        samples.forEach { s ->
            val name = s["name"]!!.jsonPrimitive.content
            val want = s["expect"]!!.jsonObject
            val blocks = MdBlocks.split(s["normalized"]!!.jsonPrimitive.content)
            val tables = blocks.filterIsInstance<MdBlock.Table>()
            val wantTables = want["tables"]!!.jsonArray.map { it.jsonObject }
            assertEquals(name, wantTables.size, tables.size)
            tables.zip(wantTables).forEach { (t, w) ->
                assertEquals(name, w["cols"]!!.jsonPrimitive.int, t.header.size)
                assertEquals(name, w["rows"]!!.jsonPrimitive.int, t.rows.size)
                w["layout"]!!.jsonObject.forEach { (width, layout) ->
                    assertEquals("$name at $width", layout.jsonPrimitive.content, TableLayouts.layout(t.header, t.rows, width.toInt()).name.lowercase())
                }
            }
            // Heading levels outside code fences: the normaliser leaves only ### and ####.
            var fenced = false
            val levels = blocks.filterIsInstance<MdBlock.Text>().flatMap { it.md.lines() }.mapNotNull { ln ->
                if (ln.trimStart().startsWith("```")) { fenced = !fenced; null }
                else if (fenced) null else Regex("^(#{1,6})\\s").find(ln)?.groupValues?.get(1)?.length
            }.distinct()
            assertEquals(name, want["headings"]!!.jsonArray.map { it.jsonPrimitive.int }, levels)
        }
    }

    @Test fun theCategoriesTableIsCardsOnAPhoneAndATableOnATablet() {
        val s = markdown["samples"]!!.jsonArray.map { it.jsonObject }.first { it["name"]!!.jsonPrimitive.content == "categories-table" }
        var width by mutableStateOf(360)
        compose.setContent { MaterialTheme { Box(Modifier.requiredWidth(width.dp)) { AgentMarkdown(s["normalized"]!!.jsonPrimitive.content) } } }
        compose.onNodeWithContentDescription("Table, 3 rows, cards").assertExists()
        assertTrue(shows("from:levi.com OR from:gap.com OR from:nike.com"))
        assertTrue(shows("Categories & matching rules"))
        width = 800
        compose.waitForIdle()
        compose.onNodeWithContentDescription("Table, 3 rows, table").assertExists()
    }

    @Test fun cellsSplitOnPipesOutsideCode() {
        assertEquals(listOf("a", "`x | y`", "b|c"), MdBlocks.cells("| a | `x | y` | b\\|c |"))
        assertEquals("Bold code [3] site".length, TableLayouts.cellChars("**Bold** `code` [3](mailr-cite:3) [site](https://x.example)"))
    }
}
