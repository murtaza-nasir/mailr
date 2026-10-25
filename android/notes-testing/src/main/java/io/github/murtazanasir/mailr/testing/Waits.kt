package io.github.murtazanasir.mailr.testing

import android.os.Looper
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.ComposeContentTestRule
import androidx.compose.ui.test.isRoot
import androidx.compose.ui.test.printToString
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.job
import org.robolectric.Shadows.shadowOf

/**
 * How long a wait polls before it reports a hang. It is a hang detector, not a performance
 * budget: a wait whose condition comes true returns at once and never reaches it. The first test
 * of a fresh JVM spends 9–17 s in class loading, the Robolectric sandbox and the first composition
 * when other suites load the machine; 5 and 10 s budgets ran out there although nothing was wrong.
 */
const val WAIT_CEILING_MS = 60_000L

/** The pause between two looks: long enough to let Room's and OkHttp's threads get on. */
private const val POLL_MS = 10L

/** Runs what the main looper holds now (screens, ViewModels, Dispatchers.Main coroutines). */
fun idleMainLooper() = shadowOf(Looper.getMainLooper()).idle()

/**
 * Waits until [check] holds. Before each look it runs [idle] (by default the main looper's
 * messages); between looks other threads (Room, OkHttp, Dispatchers.Default) get on. Fails with
 * "never: [what]" after [timeoutMs], followed by [detail] (read at the failure: the requests a fake
 * server saw, for example). Every wait in both apps' tests goes through here.
 */
fun eventually(
    what: String,
    timeoutMs: Long = WAIT_CEILING_MS,
    idle: () -> Unit = ::idleMainLooper,
    detail: () -> String = { "" },
    check: () -> Boolean,
) {
    // The clock starts after the first look: a JVM's first composition and class loading happen in it
    // (the first test of a JVM took up to 57 s with 24 test JVMs on a loaded machine) and are slow work, not a hang.
    idle()
    if (check()) return
    val start = System.nanoTime()
    while (true) {
        idle()
        if (check()) return
        val waited = (System.nanoTime() - start) / 1_000_000
        if (waited > timeoutMs) {
            val more = detail()
            throw AssertionError("never: $what (waited ${waited / 1000} s)" + if (more.isEmpty()) "" else "; $more").apply {
                // What the main looper, Compose and the threads were doing while the test waited (TestGraph's
                // own dump comes after the teardown has already disposed the screen).
                addSuppressed(AssertionError("at the timeout, " + runCatching { io.github.murtazanasir.mailr.notes.LooperDump.all() }.getOrElse { it.toString() }))
            }
        }
        Thread.sleep(POLL_MS)
    }
}

/** [eventually] with the Compose rule's idling between looks (composition, effects, the main looper). */
fun ComposeContentTestRule.eventually(
    what: String,
    timeoutMs: Long = WAIT_CEILING_MS,
    detail: () -> String = { "" },
    check: () -> Boolean,
) = eventually(what, timeoutMs, idle = { waitForIdle() }, check = check, detail = {
    detail() + "\nthe screen at the timeout:\n" + runCatching { onAllNodes(isRoot(), useUnmergedTree = true).printToString() }.getOrElse { it.toString() }
})

/**
 * Compose's own waitUntil (it moves the test clock on frame by frame between looks, where [eventually]
 * runs the rule's waitForIdle) with the shared ceiling. For screenshots taken while an animation runs.
 */
fun ComposeContentTestRule.until(what: String, condition: () -> Boolean) {
    try {
        // As in eventually, the first idle (a JVM's first composition) comes before the clock starts; the
        // condition's own node lookup would run the same waitForIdle.
        waitForIdle()
        waitUntil(what, WAIT_CEILING_MS, condition)
    } catch (e: androidx.compose.ui.test.ComposeTimeoutException) {
        throw AssertionError("never: $what (waited ${WAIT_CEILING_MS / 1000} s)", e).apply {
            addSuppressed(AssertionError("at the timeout, " + runCatching { io.github.murtazanasir.mailr.notes.LooperDump.all() }.getOrElse { it.toString() }))
            addSuppressed(AssertionError("the screen at the timeout:\n" + runCatching { onAllNodes(isRoot(), useUnmergedTree = true).printToString() }.getOrElse { it.toString() }))
        }
    }
}

/** Waits until the screen shows a node that matches [m]. */
fun ComposeContentTestRule.waitForNode(m: SemanticsMatcher, useUnmergedTree: Boolean = false) =
    eventually("a node: ${m.description}") { onAllNodes(m, useUnmergedTree).fetchSemanticsNodes().isNotEmpty() }

/** Waits until the screen shows [text] (or a text that contains it, with [substring]). */
fun ComposeContentTestRule.waitForText(text: String, substring: Boolean = false) = waitForNode(hasText(text, substring))

/** The coroutines that [scopes] run at this moment. */
private fun running(scopes: Array<out CoroutineScope>): Set<Job> =
    scopes.flatMap { s -> s.coroutineContext.job.children.filter { it.isActive }.toList() }.toSet()

/**
 * Runs [action] and waits until every coroutine it started in [scopes] has finished (those that ran
 * before it may go on: a collector, a heartbeat). A "nothing happened" check belongs after this, not
 * after a fixed sleep: once the work the action started is over, a request or write it made has
 * arrived, and one that is missing will not come later.
 */
fun settle(vararg scopes: CoroutineScope, what: String, idle: () -> Unit = ::idleMainLooper, action: () -> Unit) {
    idle()
    val before = running(scopes)
    action()
    eventually("the work of $what to finish", idle = idle, detail = { "still running: ${running(scopes) - before}" }) {
        (running(scopes) - before).isEmpty()
    }
    idle()
}

/**
 * Waits until [http] has no call in flight: OkHttp's dispatcher counts a call from `enqueue` until it
 * has its answer, so afterwards every request made so far has reached the fake server's log. For a
 * "nothing was asked" check on a screen, whose effects are not in a scope a test can see.
 */
fun noCallsInFlight(http: okhttp3.OkHttpClient, what: String, idle: () -> Unit = ::idleMainLooper) =
    eventually("$what: no request in flight", idle = idle, detail = { "${http.dispatcher.runningCallsCount()} running" }) {
        http.dispatcher.runningCallsCount() == 0 && http.dispatcher.queuedCallsCount() == 0
    }
