package io.github.murtazanasir.mailr

import android.app.Application
import android.content.res.Configuration
import androidx.test.core.app.ApplicationProvider
import java.io.File
import java.util.Locale
import javax.xml.parsers.DocumentBuilderFactory
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.w3c.dom.Element

/**
 * The translations of all four modules: every
 * language has every text English has (a text left English is written out in English, so it is
 * there on purpose), no other, the same format arguments, and the plural forms its CLDR rules use.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class TranslationsTest {
    private val modules = listOf("app", "notes", "notes-core", "session-core")
    /** Language tag → resource qualifier, as tools/i18n/glossary.json has them. */
    private val languages = mapOf("de" to "de", "es" to "es", "fr" to "fr", "pt-BR" to "pt-rBR", "ru" to "ru", "zh-Hans" to "b+zh+Hans")
    private val forms = mapOf("ru" to setOf("one", "few", "many", "other"), "zh-Hans" to setOf("other"))
    private val format = Regex("""%(?:%|(?:\d+\$)?[-#+0,(]*\d*(?:\.\d+)?[sdfxXcbBeEgGhHoaAtT])""")

    private data class Res(val strings: Map<String, String>, val plurals: Map<String, Map<String, String>>)

    private fun read(dir: File): Res {
        val strings = mutableMapOf<String, String>()
        val plurals = mutableMapOf<String, Map<String, String>>()
        dir.listFiles { f -> f.name.startsWith("strings") && f.name.endsWith(".xml") }.orEmpty().forEach { f ->
            val root = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(f).documentElement
            val nodes = root.childNodes
            for (i in 0 until nodes.length) {
                val e = nodes.item(i) as? Element ?: continue
                if (e.getAttribute("translatable") == "false") continue
                val name = e.getAttribute("name")
                when (e.tagName) {
                    "string" -> strings[name] = e.textContent
                    "plurals" -> {
                        val items = e.getElementsByTagName("item")
                        plurals[name] = (0 until items.length).associate { j ->
                            val it = items.item(j) as Element
                            it.getAttribute("quantity") to it.textContent
                        }
                    }
                }
            }
        }
        return Res(strings, plurals)
    }

    private fun args(s: String) = format.findAll(s).map { it.value }.filter { it != "%%" }.sorted().toList()

    @Test fun everyLanguageHasEveryTextWithTheSameArguments() {
        val bad = mutableListOf<String>()
        for (m in modules) {
            val en = read(File("../$m/src/main/res/values"))
            for ((lang, q) in languages) {
                val dir = File("../$m/src/main/res/values-$q")
                assertTrue("$m has no values-$q", dir.isDirectory)
                val tr = read(dir)
                bad += (en.strings.keys - tr.strings.keys).map { "$m/$q: $it missing" }
                bad += (tr.strings.keys - en.strings.keys).map { "$m/$q: $it is not in English" }
                bad += (en.plurals.keys - tr.plurals.keys).map { "$m/$q: plurals $it missing" }
                for ((name, text) in tr.strings) {
                    val e = en.strings[name] ?: continue
                    if (args(text) != args(e)) bad += "$m/$q $name: ${args(text)} but English has ${args(e)}"
                }
                val want = forms[lang] ?: setOf("one", "other")
                for ((name, items) in tr.plurals) {
                    val e = en.plurals[name] ?: continue
                    if (!items.keys.containsAll(want)) bad += "$m/$q plurals $name: has ${items.keys}, needs $want"
                    val other = args(e["other"] ?: "")
                    for ((quantity, text) in items) {
                        // German and Spanish "one" is only 1, so it may leave the number out as English does.
                        val ref = if (quantity == "one" && lang in setOf("de", "es")) args(e["one"] ?: "") else other
                        val got = args(text)
                        if (got != ref && !(quantity == "one" && lang in setOf("de", "es") && got == other)) {
                            bad += "$m/$q plurals $name/$quantity: $got but English has $ref"
                        }
                    }
                }
            }
        }
        assertEquals(emptyList<String>(), bad)
    }

    @Test fun russianPicksOneFewAndManyAndChineseHasOneForm() {
        val app = ApplicationProvider.getApplicationContext<Application>()
        fun res(tag: String) = app.createConfigurationContext(Configuration(app.resources.configuration).apply { setLocale(Locale.forLanguageTag(tag)) }).resources
        val ru = read(File("../app/src/main/res/values-ru")).plurals.getValue("mail_shown_in_n")
        for ((n, form) in listOf(1 to "one", 21 to "one", 3 to "few", 24 to "few", 5 to "many", 11 to "many")) {
            val want = ru.getValue(form).replace("\\'", "'").replace("%1\$d", "$n").replace("%2\$s", "Inbox")
            assertEquals("ru $n", want, res("ru").getQuantityString(R.plurals.mail_shown_in_n, n, n, "Inbox"))
        }
        val zh = read(File("../app/src/main/res/values-b+zh+Hans")).plurals.getValue("mail_shown_in_n")
        assertEquals(setOf("other"), zh.keys)
        assertEquals(zh.getValue("other").replace("%1\$d", "1").replace("%2\$s", "Inbox"), res("zh-CN").getQuantityString(R.plurals.mail_shown_in_n, 1, 1, "Inbox"))
        assertEquals(res("zh-Hans").getString(R.string.mail_folder_inbox), res("zh-CN").getString(R.string.mail_folder_inbox))
    }
}
