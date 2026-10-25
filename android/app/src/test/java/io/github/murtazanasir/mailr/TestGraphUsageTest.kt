package io.github.murtazanasir.mailr

import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * Guard for [TestGraph]: tests build an [AppGraph] and the app's ViewModels only through it (or
 * through CalendarHarness, which owns one). A ViewModel made directly is never cleared, so its
 * coroutines outlive the test, run against the closed database and fail in a later test
 * (`UncaughtExceptionsBeforeTest`).
 */
class TestGraphUsageTest {
    private val tests = File("src/test/java")
    private val main = File("src/main/java")

    private fun sources(root: File) = root.walkTopDown().filter { it.isFile && it.extension == "kt" }

    /** Every class in the app that extends ViewModel. */
    private fun viewModelClasses(): Set<String> {
        val decl = Regex("""class\s+(\w+)\b(?:(?!\bclass\b)[^{])*?:\s*ViewModel\(\)""", RegexOption.DOT_MATCHES_ALL)
        return sources(main).flatMap { f -> decl.findAll(f.readText()).map { it.groupValues[1] } }.toSet()
    }

    @Test fun graphsAndViewModelsAreBuiltThroughTestGraph() {
        assertTrue("run from the app module: ${tests.absolutePath}", tests.isDirectory && main.isDirectory)
        val vms = viewModelClasses()
        assertTrue("found no ViewModel classes in ${main.absolutePath}", "MailboxViewModel" in vms)
        val vmCall = Regex("""\b(${vms.joinToString("|")})\(""")
        val graphCall = Regex("""\bAppGraph\(""")
        val exempt = setOf("TestGraph.kt", "TestGraphUsageTest.kt")
        val bad = mutableListOf<String>()
        for (f in sources(tests).filter { it.name !in exempt }) {
            f.readLines().forEachIndexed { i, line ->
                val code = line.substringBefore("//").trim()
                if (code.startsWith("import ") || code.startsWith("*") || code.startsWith("/*")) return@forEachIndexed
                val where = "${f.relativeTo(tests)}:${i + 1}: $code"
                if (graphCall.containsMatchIn(code)) bad += "$where  (use TestGraph.graph())"
                if (vmCall.containsMatchIn(code) && !Regex("""\bvm\s*\{""").containsMatchIn(code)) {
                    bad += "$where  (wrap in graphs.vm { } or h.vm { })"
                }
            }
        }
        if (bad.isNotEmpty()) fail("Build graphs and ViewModels through TestGraph so they are torn down in order:\n" + bad.joinToString("\n"))
    }

    /**
     * Compose tests in both apps use io.github.murtazanasir.mailr.testing.createComposeRule (or effectsOnMainLooper()):
     * with Compose's own rule a screen's coroutine carries on in the OkHttp or Room thread that resumed
     * it, and now and then the screen never shows what it loaded.
     */
    @Test fun composeRulesKeepEffectsOnTheMainThread() {
        val roots = listOf(tests, File("../notes/src/test/java"))
        assertTrue("run from the app module", roots.all { it.isDirectory })
        val direct = Regex("""androidx\.compose\.ui\.test\.junit4\.createComposeRule\b""")
        val activityRule = Regex("""\bcreateAndroidComposeRule<[^>]*>\(\)""")
        val bad = mutableListOf<String>()
        for (root in roots) for (f in sources(root)) {
            f.readLines().forEachIndexed { i, line ->
                val where = "${f.relativeTo(root)}:${i + 1}: ${line.trim()}"
                if (direct.containsMatchIn(line)) bad += where
                if (activityRule.containsMatchIn(line) && !line.contains("effectsOnMainLooper()")) bad += "$where  (add .effectsOnMainLooper())"
            }
        }
        if (bad.isNotEmpty()) fail("Use io.github.murtazanasir.mailr.testing.createComposeRule:\n" + bad.joinToString("\n"))
    }
}
