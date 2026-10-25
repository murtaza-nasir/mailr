package io.github.murtazanasir.mailr

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Android's regex engine (ICU) accepts only the inline flags i, m, s, x and w. Java's `(?U)` and
 * `(?u)` compile fine on the JVM these tests run on, but throw PatternSyntaxException on a phone:
 * quick add crashed on the first keystroke (3 Oct 2026). Every inline flag group in any module must
 * use ICU's set, and Pattern.UNICODE_* must not appear.
 */
class AndroidRegexFlagsTest {
    @Test fun appCodeUsesOnlyIcuRegexFlags() {
        // Every module's main code: notes-core's (?U) crashed tables in both apps (8 Oct 2026) while
        // this test only read the app module.
        val project = listOf(File("."), File("..")).first { File(it, "settings.gradle.kts").isFile }
        val roots = project.listFiles().orEmpty().map { File(it, "src/main/java") }.filter { it.isDirectory }
        assertTrue("no module sources found", roots.size >= 3)
        val flagGroup = Regex("""\(\?([a-zA-Z]+)[):]""")
        val bad = mutableListOf<String>()
        roots.asSequence().flatMap { it.walk() }.filter { it.isFile && it.extension in setOf("kt", "java") }.forEach { f ->
            f.readLines().forEachIndexed { i, line ->
                flagGroup.findAll(line).forEach { m ->
                    if (m.groupValues[1].any { it !in "imsxw" }) bad += "${f.name}:${i + 1}: ${m.value}"
                }
                if ("UNICODE_CHARACTER_CLASS" in line || "UNICODE_CASE" in line) bad += "${f.name}:${i + 1}: Pattern.UNICODE_*"
            }
        }
        assertTrue("Regex flags Android rejects:\n" + bad.joinToString("\n"), bad.isEmpty())
    }
}
