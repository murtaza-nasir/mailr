package io.github.murtazanasir.mailr.notesapp

import android.content.Context
import android.os.Looper
import androidx.compose.runtime.snapshots.ObserverHandle
import androidx.compose.runtime.snapshots.Snapshot
import androidx.compose.ui.platform.AndroidUiDispatcher
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.job
import kotlinx.coroutines.test.runTest
import org.junit.rules.TestRule
import org.junit.runner.Description
import org.junit.runners.model.Statement
import org.robolectric.Shadows.shadowOf
import kotlin.coroutines.ContinuationInterceptor
import kotlin.coroutines.CoroutineContext

/**
 * The Notes app's TestGraph (the mailr app's TestGraph, for [NotesGraph]): owns the in-memory graphs,
 * the ViewModels made with [vm] and the scopes made with [scope], and tears them down in order —
 * ViewModels cleared, scopes and graph scopes cancelled, every coroutine finished with the main
 * looper idled meanwhile, then the databases closed and the [closeLast] resources shut. It also fails
 * the test that leaked a coroutine failure.
 */
class NotesTestGraph : TestRule {
    private val graphs = mutableListOf<NotesGraph>()
    private val store = ViewModelStore()
    private val jobs = mutableListOf<Job>()
    private val scopes = mutableListOf<CoroutineScope>()
    private val last = mutableListOf<AutoCloseable>()
    private var count = 0
    private var closed = false

    /**
     * The time the graphs' repository timers run on (the Notes ticks and presence): it moves only
     * when the test calls `time.advanceBy(ms)`.
     */
    val time = io.github.murtazanasir.mailr.testing.VirtualTimeline()

    fun graph(context: Context = ApplicationProvider.getApplicationContext()): NotesGraph {
        check(!closed) { "NotesTestGraph already closed" }
        return NotesGraph(context, inMemoryDb = true, time = time).also { graphs += it; jobs += it.scope.coroutineContext.job }
    }

    fun <T : ViewModel> vm(make: () -> T): T {
        check(!closed) { "NotesTestGraph already closed" }
        val made = make()
        val factory = object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <V : ViewModel> create(modelClass: Class<V>): V = made as V
        }
        ViewModelProvider(store, factory)["vm-${count++}", made.javaClass]
        jobs += made.viewModelScope.coroutineContext.job
        return made
    }

    fun scope(context: kotlin.coroutines.CoroutineContext = kotlinx.coroutines.Dispatchers.Main): CoroutineScope =
        CoroutineScope(SupervisorJob() + context).also { scopes += it; jobs += it.coroutineContext.job }

    fun <T : AutoCloseable> closeLast(c: T): T = c.also { last += it }

    fun close() {
        if (closed) return
        closed = true
        try {
            store.clear()
            scopes.forEach { it.cancel() }
            graphs.forEach { it.live.stop(); it.scope.cancel() }
            drain()
        } finally {
            graphs.forEach { runCatching { it.db.close() } }
            last.forEach { runCatching { it.close() } }
        }
    }

    /** Waits for every coroutine to finish; the ceiling detects a hang, it is not a budget. */
    private fun drain() {
        io.github.murtazanasir.mailr.testing.eventually("coroutines finish after their ViewModels and scopes were cancelled") { jobs.all { it.isCompleted } }
        shadowOf(Looper.getMainLooper()).idle()
    }

    override fun apply(base: Statement, description: Description): Statement = object : Statement() {
        override fun evaluate() {
            assertNoLeakedFailures("A test that ran before ${description.displayName}")
            assertComposeStillApplies(description.displayName)
            var failure: Throwable? = null
            try {
                base.evaluate()
            } catch (t: Throwable) {
                failure = t
                // A screen that never showed what it loaded: what the main looper still held then.
                if (t is AssertionError || t.javaClass.simpleName.contains("Timeout")) {
                    t.addSuppressed(AssertionError("at the failure, " + io.github.murtazanasir.mailr.notes.LooperDump.all()))
                }
            }
            try {
                close()
            } catch (t: Throwable) {
                if (failure == null) failure = t else failure.addSuppressed(t)
            }
            try {
                assertNoLeakedFailures(description.displayName)
            } catch (t: Throwable) {
                if (failure == null) failure = t else failure.addSuppressed(t)
            }
            watchLateWrites(description.displayName)
            failure?.let { throw it }
        }
    }

    companion object {
        private val lateWrites = mutableListOf<String>()
        private var watcher: ObserverHandle? = null

        /**
         * From the end of a test's teardown to the start of the next test, records every write to
         * Compose state. Such a write comes from a coroutine that outlived its screen (one that caught
         * its CancellationException, for example). Robolectric clears the main looper between tests, and
         * if it clears the message that write posted, Compose never applies global snapshot changes again
         * in that JVM: every later Compose test then fails with "Compose did not get idle".
         */
        private fun watchLateWrites(test: String) {
            watcher?.dispose()
            watcher = Snapshot.registerGlobalWriteObserver { state ->
                synchronized(lateWrites) {
                    if (lateWrites.size < 20) {
                        val frames = Throwable().stackTrace.filter { it.className.startsWith("io.github.murtazanasir.") && !it.className.contains("TestGraph") }.take(4)
                        lateWrites += "after $test: ${state.javaClass.simpleName} on ${Thread.currentThread().name} at ${frames.joinToString(" < ")}"
                    }
                }
            }
        }

        /**
         * Fails when Compose's main dispatcher is wedged (see [watchLateWrites]): it holds work and
         * believes a message to run it is queued, but the main looper holds none. The Compose state
         * written since the last teardown is listed as the suspects. Without this check the first
         * Compose test that writes state outside composition hangs for a minute instead.
         */
        fun assertComposeStillApplies(test: String) {
            watcher?.dispose()
            watcher = null
            val late = synchronized(lateWrites) { lateWrites.toList().also { lateWrites.clear() } }
            if (!composeMainWedged()) return
            throw AssertionError("Before $test, Compose's main dispatcher stopped running work in this JVM, so Compose " +
                "tests after it never go idle. A coroutine that outlived its screen wrote Compose state after a test's " +
                "teardown (a catch that swallowed its CancellationException?). Writes since the last teardown:\n" +
                late.joinToString("\n").ifEmpty { "(none recorded: the write came before the watch started)" })
        }

        /**
         * Reads AndroidUiDispatcher.Main's private state; it never makes or drives the dispatcher (made
         * here, before a Compose rule, it changes how later Compose tests run). False when no Compose
         * test in this JVM has made it, or when a Compose upgrade renamed the field.
         */
        private fun composeMainWedged(): Boolean = try {
            val main = AndroidUiDispatcher::class.java.getDeclaredField("Main\$delegate").apply { isAccessible = true }.get(null) as Lazy<*>
            val d = if (main.isInitialized()) (main.value as CoroutineContext)[ContinuationInterceptor] else null
            d != null && d.javaClass.getDeclaredField("scheduledTrampolineDispatch").apply { isAccessible = true }.getBoolean(d) &&
                shadowOf(Looper.getMainLooper()).isIdle
        } catch (e: ReflectiveOperationException) {
            false
        }

        fun assertNoLeakedFailures(test: String) {
            try {
                runTest { }
            } catch (e: IllegalStateException) {
                if (e.javaClass.simpleName != "UncaughtExceptionsBeforeTest") throw e
                throw AssertionError("$test leaked a coroutine that failed after the test stopped watching it", e)
            }
        }
    }
}
