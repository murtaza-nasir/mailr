package io.github.murtazanasir.mailr

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.auth.SessionStore
import kotlinx.coroutines.runBlocking
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * A unit-level proxy for cold start: how long the main thread spends building the app graph, and how
 * long until what a background worker needs (the session, the mail repository, an open database and
 * the Notes module) is ready. Robolectric has no Keystore, so the session's unseal is not in these
 * numbers. Run alone (`--tests io.github.murtazanasir.mailr.ColdStartProxyTest`) for a fresh JVM; it prints
 * `cold-start-proxy:` lines and checks nothing about the time.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ColdStartProxyTest {
    @get:org.junit.Rule val graphs = TestGraph()

    @Test fun timeToGraphReady() {
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        SessionStore(ctx).apply { server = "https://mail.example.org"; save("tok", "csrf", "me@example.com", "Me") }
        val rounds = (0 until 6).map {
            val t0 = System.nanoTime()  // clock-ok: measurement
            val g = graphs.graph(ctx)
            val built = System.nanoTime()  // clock-ok: measurement
            g.sessions.session.value
            runBlocking { g.mail; g.db.pending().calendarCount(); g.notes }
            val ready = System.nanoTime()  // clock-ok: measurement
            (built - t0) / 1_000_000.0 to (ready - t0) / 1_000_000.0
        }
        val (cold, warm) = rounds.first() to rounds.drop(1)
        println("cold-start-proxy: first graph: main thread %.1f ms, worker-ready %.1f ms".format(cold.first, cold.second))
        println("cold-start-proxy: warm median: main thread %.2f ms, worker-ready %.2f ms".format(
            warm.map { it.first }.sorted()[warm.size / 2], warm.map { it.second }.sorted()[warm.size / 2]))
    }

    /** A background wake that needs the mail queue builds neither the recorder, the calendar, the reminders nor the live stream. */
    @Test fun theGraphBuildsOnlyWhatIsUsed() {
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        val g = graphs.graph(ctx)
        org.junit.Assert.assertFalse(g.dbLazy.isInitialized())
        runBlocking { g.mail; g.db.pending().calendarCount() }
        for ((name, lazy) in listOf("recorder" to g.recorderLazy, "calendar" to g.calendarLazy, "reminders" to g.remindersLazy, "live" to g.liveLazy)) {
            org.junit.Assert.assertFalse("$name was built", lazy.isInitialized())
        }
    }
}
