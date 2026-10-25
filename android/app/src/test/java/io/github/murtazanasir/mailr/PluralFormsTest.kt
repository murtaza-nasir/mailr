package io.github.murtazanasir.mailr

import java.io.File
import javax.xml.parsers.DocumentBuilderFactory
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element

/**
 * English plurals of all four modules: a `one` form never reads as a plural ("1 events", "Delete 1
 * tasks?"), and a count before a plural noun lives in a <plurals>, not a plain string.
 */
class PluralFormsTest {
    private val modules = listOf("app", "notes", "notes-core", "session-core")
    // A count followed by a plural noun: "%d events", "%1$d of %2$d subtasks done", "Delete %d tasks?".
    private val countedPlural = Regex("""%(\d\$)?d (?:[a-z]+ )?(?:[a-z]+ )?[a-z]+(?<!s)s\b""")
    // Words ending in s that are not plurals here.
    private val notPlural = Regex("""\b(?:is|was|has|its|this|always|across|status|address|yes|less|ago|plus|bus|process|progress|needs|works|counts|comes|goes|makes|takes|shows|stays|keeps|waits|opens|runs)\b""")

    private fun files() = modules.flatMap { m ->
        File("../$m/src/main/res/values").listFiles { f -> f.name.endsWith(".xml") }?.toList().orEmpty()
    }

    private fun parse(f: File): Element = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(f).documentElement

    private fun plural(text: String): Boolean = countedPlural.findAll(text).any { m -> !notPlural.containsMatchIn(m.value.substringAfterLast(' ')) }

    @Test fun oneFormsAreSingular() {
        val bad = mutableListOf<String>()
        files().forEach { f ->
            val items = parse(f).getElementsByTagName("plurals")
            for (i in 0 until items.length) {
                val p = items.item(i) as Element
                val forms = p.getElementsByTagName("item")
                for (j in 0 until forms.length) {
                    val it = forms.item(j) as Element
                    if (it.getAttribute("quantity") == "one" && plural(it.textContent)) bad += "${f.name} ${p.getAttribute("name")}: ${it.textContent}"
                }
            }
        }
        assertTrue("one forms that read as plurals:\n" + bad.joinToString("\n"), bad.isEmpty())
    }

    @Test fun countsBeforePluralNounsArePlurals() {
        val bad = mutableListOf<String>()
        files().forEach { f ->
            val strings = parse(f).getElementsByTagName("string")
            for (i in 0 until strings.length) {
                val s = strings.item(i) as Element
                if (plural(s.textContent)) bad += "${f.name} ${s.getAttribute("name")}: ${s.textContent}"
            }
        }
        assertTrue("counted plurals in plain strings (use <plurals>):\n" + bad.joinToString("\n"), bad.isEmpty())
    }
}
