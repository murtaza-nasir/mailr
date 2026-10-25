package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.util.catching
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * `runCatching` in a coroutine swallows the CancellationException that ends it; the apps use
 * [catching] (notes-core, io.github.murtazanasir.mailr.util), which lets it through. This checks the helper and
 * scans the main Kotlin of both apps and the shared modules for a `runCatching`.
 */
class RunCatchingTest {
    @Test fun catchingLetsCancellationThrough() = runBlocking {
        val after = mutableListOf<String>()
        val started = CompletableDeferred<Unit>()
        val job = launch(start = CoroutineStart.UNDISPATCHED) {
            catching { started.complete(Unit); awaitCancellation() }
            after += "went on after cancel"
        }
        started.await()
        job.cancelAndJoin()
        assertTrue(after.toString(), after.isEmpty())
        // Other failures are still caught.
        assertTrue(catching<Int> { error("boom") }.isFailure)
        assertEquals(2, catching { 1 + 1 }.getOrNull())
    }

    @Test fun runCatchingWouldHaveGoneOn() = runBlocking {
        val after = mutableListOf<String>()
        val started = CompletableDeferred<Unit>()
        val job = launch(start = CoroutineStart.UNDISPATCHED) {
            runCatching { started.complete(Unit); awaitCancellation() }
            after += "went on after cancel"
        }
        started.await()
        job.cancelAndJoin()
        assertEquals(listOf("went on after cancel"), after)
    }

    @Test fun noRunCatchingInMainCode() {
        val root = File("..")
        val found = listOf("app", "notes", "notes-core", "session-core").flatMap { m ->
            File(root, "$m/src/main/java").walkTopDown().filter { it.isFile && it.extension == "kt" }.flatMap { f ->
                f.readLines().mapIndexedNotNull { i, line ->
                    val code = line.substringBefore("//").trim()
                    if (code.startsWith("*") || code.startsWith("/*")) null
                    else if (Regex("""\brunCatching\s*\{""").containsMatchIn(code)) "${f.relativeTo(root).path}:${i + 1}: ${line.trim()}" else null
                }
            }
        }
        assertFalse("use catching { } (io.github.murtazanasir.mailr.util) instead of runCatching:\n" + found.joinToString("\n"), found.isNotEmpty())
    }
}
