package io.github.murtazanasir.mailr.testing

import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.time.Timeline
import kotlinx.coroutines.CancellableContinuation
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/**
 * A [Timeline] that moves only when the test moves it ([advanceBy]). TestGraph and NotesTestGraph
 * build every graph on one, so a repository timer (the 4 s "✓ Cleo" tick, presence's 40 s) neither
 * ends early on a slow machine nor needs a real wait: the test advances past it and then waits for
 * what follows.
 *
 * [now] is [AppClock]'s instant plus the time advanced. One [advanceBy] resumes each timer that falls
 * due in it once, in order of its due time; a loop that waits again (a heartbeat) waits from the new
 * time, so step in smaller amounts to see each beat.
 */
class VirtualTimeline : Timeline {
    private class Timer(val at: Long, val cont: CancellableContinuation<Unit>)

    private val lock = Any()
    private var advanced = 0L
    private val timers = mutableListOf<Timer>()

    override fun now(): Long = AppClock.millis() + synchronized(lock) { advanced }

    override suspend fun delay(ms: Long) {
        if (ms <= 0) return
        suspendCancellableCoroutine { c ->
            val t = synchronized(lock) { Timer(advanced + ms, c).also { timers += it } }
            c.invokeOnCancellation { synchronized(lock) { timers.remove(t) } }
        }
    }

    /** Timers waiting on this timeline now. */
    val waiting: Int get() = synchronized(lock) { timers.size }

    /** Moves the time on by [ms] and resumes the timers that fall due. */
    fun advanceBy(ms: Long) {
        require(ms >= 0) { "time only moves forward" }
        val due = synchronized(lock) {
            advanced += ms
            timers.filter { it.at <= advanced }.also { timers.removeAll(it) }
        }
        due.sortedBy { it.at }.forEach { it.cont.resume(Unit) }
    }
}
