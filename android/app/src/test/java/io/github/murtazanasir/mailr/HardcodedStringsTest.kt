package io.github.murtazanasir.mailr

import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * Text a person reads lives in string resources, so it can be translated. This
 * scans the Kotlin of both apps and the shared modules for string literals in the
 * places that show text: `Text("…")`, named arguments such as `label = "…"`, `title = "…"`,
 * `contentDescription = "…"`, and the notification, toast and snackbar calls.
 *
 * It is a pragmatic check, not a parser: it reads one line at a time. A literal counts when it holds
 * a word (two letters in a row) outside `${…}`. Exemptions:
 * - a line that ends with `// i18n-ignore` (say why after it), or the line after a line that is only
 *   `// i18n-ignore`;
 * - the product name alone ("Mailr");
 * - files in [EXEMPT_FILES] (debug-only screens).
 */
class HardcodedStringsTest {
    private val modules = listOf("app", "notes", "notes-core", "session-core")

    companion object {
        /** Whole files the check skips, with the reason. */
        val EXEMPT_FILES = mapOf<String, String>()

        /** Literals that may stand as they are. */
        val ALLOWED = setOf("Mailr")

        private const val LIT = """"((?:[^"\\]|\\.)*)""""
        /** Call sites whose string argument is shown to the user. */
        val PATTERNS = listOf(
            Regex("""\bText\(\s*(?:text\s*=\s*)?$LIT"""),
            Regex("""\b\w*(?:[tT]ext|[lL]abel|[tT]itle|[dD]escription|[mM]essage|[hH]int|[pP]laceholder|[sS]ubtitle|[cC]aption|[tT]oast|[sS]nack|[eE]rror)\s*=\s*$LIT"""),
            Regex("""\b(?:showSnackbar|setContentTitle|setContentText|setSubText|setTicker|setBigContentTitle|setSummaryText|bigText)\(\s*$LIT"""),
            Regex("""\bToast\.makeText\([^,]+,\s*$LIT"""),
            Regex("""\baddAction\([^,]+,\s*$LIT"""),
        )
        private val WORD = Regex("""[A-Za-z]{2,}""")
        private val TEMPLATE = Regex("""\$\{[^}]*}|\$\w+""")

        /** The user-visible literals on one line of Kotlin. */
        fun offenders(line: String): List<String> {
            val code = line.trim()
            if (code.startsWith("//") || code.startsWith("*") || code.startsWith("/*") || code.startsWith("import ")) return emptyList()
            if (line.contains("// i18n-ignore")) return emptyList()
            return PATTERNS.flatMap { p -> p.findAll(line).map { it.groupValues[1] } }
                .filter { lit -> lit !in ALLOWED && WORD.containsMatchIn(TEMPLATE.replace(lit, "")) }
                .distinct()
        }
    }

    @Test fun theCheckFindsWhatItShould() {
        assertTrue(offenders("""Text("Inbox")""").isNotEmpty())
        assertTrue(offenders("""    label = { Text(text = "Save draft") },""").isNotEmpty())
        assertTrue(offenders("""contentDescription = "Back",""").isNotEmpty())
        assertTrue(offenders("""snackbar.showSnackbar("Archived ${'$'}n")""").isNotEmpty())
        assertTrue(offenders("""Text(stringResource(R.string.mail_inbox))""").isEmpty())
        assertTrue(offenders("""Text("${'$'}{n}/${'$'}{total}")""").isEmpty())
        assertTrue(offenders("""Text(" · ")""").isEmpty())
        assertTrue(offenders("""Text("Mailr")""").isEmpty())
        assertTrue(offenders("""Text("Debug only") // i18n-ignore: debug screen""").isEmpty())
        assertTrue(offenders("""// Text("Inbox") in a comment""").isEmpty())
    }

    @Test fun userVisibleStringsAreResources() {
        val root = File("..")
        val bad = mutableListOf<String>()
        var files = 0
        for (m in modules) {
            val src = File(root, "$m/src/main/java")
            assertTrue("module sources not found: ${src.absolutePath}", src.isDirectory)
            src.walkTopDown().filter { it.isFile && it.extension == "kt" }.forEach { f ->
                files++
                val rel = f.relativeTo(root).path
                if (rel in EXEMPT_FILES) return@forEach
                var ignoreNext = false
                f.readLines().forEachIndexed { i, line ->
                    if (ignoreNext) { ignoreNext = false; return@forEachIndexed }
                    if (line.trim().startsWith("// i18n-ignore")) { ignoreNext = true; return@forEachIndexed }
                    offenders(line).forEach { bad += "$rel:${i + 1}: \"$it\"" }
                }
            }
        }
        assertTrue("scanned only $files files", files > 200)
        if (bad.isNotEmpty()) fail(
            "${bad.size} hard-coded user-visible strings; move them to strings.xml " +
                "(or end the line with // i18n-ignore and the reason):\n" + bad.joinToString("\n"))
    }
}
