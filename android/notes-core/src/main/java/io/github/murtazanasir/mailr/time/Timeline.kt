package io.github.murtazanasir.mailr.time

/**
 * The time a repository's own timers run on: "now" and a wait. The apps build their graphs on
 * [Timeline.Real] ([AppClock] and the coroutine delay). Unit tests give the graph a virtual timeline
 * (`VirtualTimeline` in android/notes-testing) and move it on themselves: a label that lasts 4 s stays
 * until the test moves the time past 4 s, however slow the machine is.
 *
 * Used for the timers whose length is what the user sees (the "✓ Cleo" tick, presence); short
 * internal debounces keep the real delay.
 */
interface Timeline {
    /** Milliseconds since the epoch, as [AppClock.millis]. */
    fun now(): Long

    /** Suspends for [ms] milliseconds of this timeline's time. */
    suspend fun delay(ms: Long)

    /** The device's time: [AppClock] and kotlinx.coroutines' delay. */
    object Real : Timeline {
        override fun now(): Long = AppClock.millis()
        override suspend fun delay(ms: Long) = kotlinx.coroutines.delay(ms)
    }
}
