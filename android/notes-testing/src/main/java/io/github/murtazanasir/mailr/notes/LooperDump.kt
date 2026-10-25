package io.github.murtazanasir.mailr.notes

import android.os.Looper
import android.os.Message
import android.os.SystemClock

/**
 * What the main looper holds and what every thread is doing, for the report of a test that timed out
 * waiting for a screen. Each queued message is listed with how far ahead of the (Robolectric) clock it
 * is due, its handler and its callback; a sync barrier shows as `barrier`.
 */
object LooperDump {
    fun mainLooper(): String = buildString {
        val now = SystemClock.uptimeMillis()
        append("main looper at uptime ").append(now).append(" ms:\n")
        try {
            val queue = Looper.getMainLooper().queue
            val head = queue.javaClass.getDeclaredField("mMessages").apply { isAccessible = true }
            val next = Message::class.java.getDeclaredField("next").apply { isAccessible = true }
            val whenF = Message::class.java.getDeclaredField("when").apply { isAccessible = true }
            synchronized(queue) {
                var m = head.get(queue) as Message?
                var n = 0
                while (m != null && n < 50) {
                    val due = whenF.getLong(m) - now
                    val target = m.target
                    append("  +").append(due).append(" ms ")
                    if (target == null) append("barrier") else {
                        append(target.javaClass.name)
                        m.callback?.let { append(" cb=").append(it.javaClass.name).append(" ").append(it.toString().take(160)) }
                        append(" what=").append(m.what)
                        if (m.isAsynchronous) append(" async")
                    }
                    append('\n')
                    m = next.get(m) as Message?
                    n++
                }
                if (n == 0) append("  (empty)\n")
            }
        } catch (e: Exception) {
            append("  (could not read the queue: ").append(e).append(")\n")
        }
    }

    fun threads(filter: (StackTraceElement) -> Boolean = { true }): String = buildString {
        for ((t, st) in Thread.getAllStackTraces()) {
            if (st.isEmpty()) continue
            if (st.none(filter)) continue
            append("thread ").append(t.name).append(" (").append(t.state).append(")\n")
            st.take(25).forEach { append("    at ").append(it).append('\n') }
        }
    }

    /** The main looper's queue, then the threads with a frame of this app on their stack. */
    /** AndroidUiDispatcher.Main's flags and queues (read by reflection; it is never created here). */
    fun composeMain(): String = try {
        val c = Class.forName("androidx.compose.ui.platform.AndroidUiDispatcher")
        val lazy = c.getDeclaredField("Main\$delegate").apply { isAccessible = true }.get(null) as Lazy<*>
        if (!lazy.isInitialized()) "compose main: not made\n" else {
            val ctx = lazy.value as kotlin.coroutines.CoroutineContext
            val d = ctx[kotlin.coroutines.ContinuationInterceptor]!!
            fun f(n: String) = d.javaClass.getDeclaredField(n).apply { isAccessible = true }.get(d)
            "compose main: scheduledTrampolineDispatch=${f("scheduledTrampolineDispatch")} scheduledFrameDispatch=${f("scheduledFrameDispatch")} " +
                "trampolined=${(f("toRunTrampolined") as Collection<*>).size} onFrame=${(f("toRunOnFrame") as List<*>).size}\n"
        }
    } catch (e: Throwable) { "compose main: unreadable ($e)\n" }

    /** The running recomposers (state, pending work) and whether the global snapshot holds unapplied changes. */
    fun recomposers(): String = try {
        val rc = Class.forName("androidx.compose.runtime.Recomposer")
        val companion = rc.getDeclaredField("Companion").get(null)
        val flow = companion.javaClass.getMethod("getRunningRecomposers").invoke(companion) as kotlinx.coroutines.flow.StateFlow<*>
        val infos = flow.value as Set<*>
        val snap = Class.forName("androidx.compose.runtime.snapshots.Snapshot")
        val sc = snap.getDeclaredField("Companion").get(null)
        val cur = sc.javaClass.getMethod("getCurrent").invoke(sc)
        val pending = cur.javaClass.getMethod("hasPendingChanges").invoke(cur)
        "recomposers: " + infos.joinToString { i ->
            val pw = i!!.javaClass.methods.firstOrNull { it.name == "getHasPendingWork" }?.apply { isAccessible = true }?.invoke(i)
            val outer = i.javaClass.declaredFields.firstOrNull { it.name.startsWith("this") }?.apply { isAccessible = true }?.get(i)
            val st = outer?.javaClass?.methods?.firstOrNull { it.name == "getCurrentState" }?.invoke(outer)
            val value = (st as? kotlinx.coroutines.flow.StateFlow<*>)?.value
            "state=$value pendingWork=$pw"
        } + "; global snapshot pending changes=$pending (on ${Thread.currentThread().name})\n"
    } catch (e: Throwable) { "recomposers: unreadable ($e)\n" }

    fun all(): String = mainLooper() + composeMain() + recomposers() + "fake server, last requests:\n" + (FakeNotesServer.current?.recent() ?: "(none)") + "\n" + threads { it.className.startsWith("io.github.murtazanasir.") || it.className.startsWith("kotlinx.coroutines") }
}
