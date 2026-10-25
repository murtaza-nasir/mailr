package io.github.murtazanasir.mailr.ui.present

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.click
import androidx.compose.ui.test.hasText
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.text.TextLayoutResult
import io.github.murtazanasir.mailr.TestGraph
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Citation numbers in an assistant answer open their source, on a phone
 * too. The answer streams in, so the text first has no links; the number that
 * arrives later must still respond to a tap. TalkBack offers each source as an action.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class AgentMarkdownLinksTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private fun tapText(fragment: String) {
        val node = compose.onNode(hasText(fragment, substring = true))
        val layouts = mutableListOf<TextLayoutResult>()
        node.fetchSemanticsNode().config[SemanticsActions.GetTextLayoutResult].action!!(layouts)
        val text = layouts[0].layoutInput.text.text
        val box = layouts[0].getBoundingBox(text.indexOf(fragment) + fragment.length / 2)
        node.performTouchInput { click(box.center) }
        compose.waitForIdle()
    }

    @Test fun aCitationThatStreamedInOpensItsSource() {
        var md by mutableStateOf("Tomorrow you have five items:")
        val opened = mutableListOf<Int>()
        compose.setContent {
            MaterialTheme {
                AgentMarkdown(md, streaming = true, onCitation = { opened += it }, citationLabel = { "Homelab backups" })
            }
        }
        compose.waitForIdle()
        md = "Tomorrow you have five items:\n\n- **12:00 AM** Homelab: re-enable encrypted backups [1](mailr-cite:1)\n- **10:00 AM** DATA 520 [2](mailr-cite:2)"
        compose.waitForIdle()
        tapText("${CHIP_PAD}2$CHIP_PAD")
        tapText("${CHIP_PAD}1$CHIP_PAD")
        assertEquals(listOf(2, 1), opened)
        val actions = compose.onRoot().fetchSemanticsNode().let { root ->
            generateSequence(listOf(root)) { l -> l.flatMap { it.children }.ifEmpty { null } }.flatten()
                .mapNotNull { it.config.getOrNull(SemanticsActions.CustomActions) }.flatten().map { it.label }.toList()
        }
        assertTrue(actions.toString(), "Open source 1: Homelab backups" in actions && "Open source 2: Homelab backups" in actions)
    }


    /**
     * Chip 9 at the end of a line lost its right side, because its padding
     * was an en space and a line drops trailing spaces. A run of chips forces lines to end on one.
     */
    @Test fun aChipAtTheEndOfALineKeepsItsPadding() {
        compose.setContent { MaterialTheme { AgentMarkdown("Sources " + (1..60).joinToString(" ") { "[$it](mailr-cite:$it)" }, onCitation = {}) } }
        compose.waitForIdle()
        val node = compose.onNode(hasText("Sources", substring = true))
        val layouts = mutableListOf<TextLayoutResult>()
        node.fetchSemanticsNode().config[SemanticsActions.GetTextLayoutResult].action!!(layouts)
        val layout = layouts[0]
        val text = layout.layoutInput.text.text
        assertTrue("the chips wrap", layout.lineCount > 1)
        for (line in 0 until layout.lineCount - 1) {
            val visible = text.substring(layout.getLineStart(line), layout.getLineEnd(line, visibleEnd = true))
            assertTrue("line $line ends on a padded chip: \"$visible\"", visible.endsWith(CHIP_PAD))
        }
    }

    @Test fun ordinaryLinksAndRawMarkersRender() {
        compose.setContent { MaterialTheme { AgentMarkdown(CiteGrammar.clean("See [[event:x@2026-05-20 \"Walk\"]] and [the guide](https://example.com).")) } }
        compose.waitForIdle()
        val all = compose.onRoot().fetchSemanticsNode().let { root ->
            generateSequence(listOf(root)) { l -> l.flatMap { it.children }.ifEmpty { null } }.flatten()
                .mapNotNull { it.config.getOrNull(SemanticsProperties.Text) }.flatten().joinToString(" ") { it.text }
        }
        assertTrue(all, "See and the guide." in all && "[[" !in all)
    }
}
