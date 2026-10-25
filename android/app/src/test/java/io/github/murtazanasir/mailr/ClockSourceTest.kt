package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.time.LocalDate
import java.time.ZoneId

/**
 * "Now" comes from one place, AppClock (notes-core), so tests and the screenshot references can
 * pin it (the docs demo's Fri 2 Oct 2026 10:40 in America/Chicago). This scans the Kotlin of both
 * apps and the shared modules for a direct read of the system clock: `System.currentTimeMillis()`,
 * `Instant.now()`, `LocalDate.now()`, `ZonedDateTime.now()` and the like.
 *
 * A read that must stay on the real clock (logging, timeouts and throttles, alarms, notification
 * timestamps, file names, ids) ends its line with `// clock-ok: <reason>`.
 */
class ClockSourceTest {
    private val modules = listOf("app", "notes", "notes-core", "session-core")

    companion object {
        /** The clock itself. */
        private val EXEMPT_FILES = setOf("notes-core/src/main/java/io/github/murtazanasir/mailr/time/AppClock.kt")

        private val PATTERNS = listOf(
            Regex("""\bSystem\s*(?:\.|::)\s*currentTimeMillis\b"""),
            Regex("""\b(?:Instant|LocalDate|LocalDateTime|LocalTime|ZonedDateTime|OffsetDateTime|OffsetTime|YearMonth|Year|MonthDay)\s*\.\s*now\s*\("""),
            Regex("""\bClock\s*\.\s*(?:systemDefaultZone|systemUTC|system)\s*\("""),
            Regex("""\bCalendar\s*\.\s*getInstance\s*\("""),
            Regex("""\bjava\.util\.Date\(\s*\)|(?<![\w.])Date\(\s*\)"""),
        )
        private val EXEMPT = Regex("""//\s*clock-ok:\s*\S""")

        /** The direct clock reads on one line of Kotlin. */
        fun offenders(line: String): List<String> {
            val code = line.trim()
            if (code.startsWith("//") || code.startsWith("*") || code.startsWith("/*") || code.startsWith("import ")) return emptyList()
            if (EXEMPT.containsMatchIn(line)) return emptyList()
            val beforeComment = line.substringBefore("//")
            return PATTERNS.flatMap { p -> p.findAll(beforeComment).map { it.value } }
        }
    }

    @Test fun theCheckFindsWhatItShould() {
        assertTrue(offenders("val t = System.currentTimeMillis()").isNotEmpty())
        assertTrue(offenders("private val now: () -> Long = System::currentTimeMillis,").isNotEmpty())
        assertTrue(offenders("val d = LocalDate.now(zone)").isNotEmpty())
        assertTrue(offenders("val d = java.time.ZonedDateTime.now()").isNotEmpty())
        assertTrue(offenders("val i = Instant.now().plusSeconds(5)").isNotEmpty())
        assertTrue(offenders("val c = Calendar.getInstance()").isNotEmpty())
        assertTrue(offenders("val d = java.util.Date()").isNotEmpty())
        assertTrue(offenders("val t = System.currentTimeMillis() // clock-ok: request timeout").isEmpty())
        assertTrue(offenders("val t = System.currentTimeMillis() // clock-ok:").isNotEmpty())
        assertTrue(offenders("val d = AppClock.today(zone)").isEmpty())
        assertTrue(offenders("val d = java.util.Date(file.lastModified())").isEmpty())
        assertTrue(offenders("// LocalDate.now() in a comment").isEmpty())
    }

    @Test fun nowComesFromAppClock() {
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
                f.readLines().forEachIndexed { i, line ->
                    offenders(line).forEach { bad += "$rel:${i + 1}: $it" }
                }
            }
        }
        assertTrue("scanned only $files files", files > 200)
        if (bad.isNotEmpty()) fail(
            "${bad.size} direct reads of the system clock; use AppClock (io.github.murtazanasir.mailr.time) " +
                "or end the line with // clock-ok: and the reason:\n" + bad.joinToString("\n"))
    }

    /** The test JVMs start pinned to the web docs' demo time (app/build.gradle.kts). */
    @Test fun testsRunAtTheDemoTime() {
        assertTrue(AppClock.pinned)
        assertEquals(ZoneId.of("America/Chicago"), ZoneId.systemDefault())
        assertEquals(LocalDate.of(2026, 10, 2), AppClock.today())
        assertEquals("2026-10-02T10:40-05:00[America/Chicago]", AppClock.now().toString())
        assertEquals(AppClock.now().toInstant().toEpochMilli(), AppClock.millis())
    }

    @Test fun pinAndReset() {
        val at = java.time.Instant.parse("2027-01-15T12:00:00Z")
        AppClock.pin(at)
        try {
            assertEquals(at.toEpochMilli(), AppClock.millis())
            assertEquals(LocalDate.of(2027, 1, 15), AppClock.today(ZoneId.of("UTC")))
        } finally {
            AppClock.reset()
        }
        assertFalse(AppClock.millis() == at.toEpochMilli())
        assertEquals(LocalDate.of(2026, 10, 2), AppClock.today())
    }
}
