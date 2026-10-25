package io.github.murtazanasir.mailr

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import androidx.work.WorkManager
import androidx.work.testing.WorkManagerTestInitHelper
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.push.PushInfo
import io.github.murtazanasir.mailr.data.push.PushStatus
import io.github.murtazanasir.mailr.data.sync.BackgroundSchedule
import io.github.murtazanasir.mailr.data.sync.backgroundSync
import io.github.murtazanasir.mailr.notes.FakeNotesServer
import io.github.murtazanasir.mailr.time.AppClock
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.concurrent.TimeUnit

/**
 * Background wakes: the calendar sync and the reminder watchdog run
 * every 15 minutes only while push does not work, hourly with working push (registered and a message
 * within three days); notes are pulled in the background by one app only.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class BackgroundScheduleTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    private val ctx get() = ApplicationProvider.getApplicationContext<Application>()
    private val day = 24L * 60 * 60 * 1000

    @Before fun setUp() {
        // Workers here do nothing: the jobs' periods are what this test reads.
        val idle = object : androidx.work.WorkerFactory() {
            override fun createWorker(appContext: android.content.Context, workerClassName: String, workerParameters: androidx.work.WorkerParameters) =
                object : androidx.work.Worker(appContext, workerParameters) { override fun doWork() = Result.success() }
        }
        WorkManagerTestInitHelper.initializeTestWorkManager(ctx, androidx.work.Configuration.Builder()
            .setExecutor(androidx.work.testing.SynchronousExecutor()).setWorkerFactory(idle).build())
    }

    private fun connected(lastMessageAt: Long?) = PushInfo(status = PushStatus.CONNECTED, targetId = 7, lastMessageAt = lastMessageAt)

    private fun minutes(name: String): Long {
        val all = WorkManager.getInstance(ctx).getWorkInfosForUniqueWork(name).get()
        val info = all.singleOrNull { !it.state.isFinished } ?: throw AssertionError("$name: $all")
        return TimeUnit.MILLISECONDS.toMinutes(info.periodicityInfo!!.repeatIntervalMillis)
    }

    private fun signedInGraph(server: String = "https://mail.example.org"): AppGraph {
        SessionStore(ctx).apply { this.server = server; save("tok", "csrf", "me@example.com", "Me") }
        return graphs.graph(ctx)
    }

    @Test fun withWorkingPushTheCalendarAndWatchdogRunHourly() {
        val g = signedInGraph()
        g.push.store.update { connected(AppClock.millis() - 60_000) }
        g.startCalendarWork()
        assertEquals(60, minutes("cal-sync"))
        assertEquals(60, minutes("reminder-tick"))
    }

    @Test fun withoutWorkingPushTheyRunEveryFifteenMinutes() {
        val g = signedInGraph()
        // Registered, but nothing arrived for four days: a distributor the phone stopped.
        g.push.store.update { connected(AppClock.millis() - 4 * day) }
        g.startCalendarWork()
        assertEquals(15, minutes("cal-sync"))
        assertEquals(15, minutes("reminder-tick"))
        // A message arrives: push works again, the safety net slows down.
        g.push.store.update { it.copy(lastMessageAt = AppClock.millis()) }
        g.followBackgroundCadence()
        assertEquals(60, minutes("cal-sync"))
        assertEquals(60, minutes("reminder-tick"))
    }

    @Test fun wakesPerDay() {
        val now = AppClock.millis()
        val working = BackgroundSchedule.plan(connected(now), now, weather = true, notesApp = true)
        val broken = BackgroundSchedule.plan(PushInfo(), now, weather = true, notesApp = true)
        // Before 14 Oct 2026 the calendar sync and the watchdog ran every 15 minutes whatever push did:
        // 24 + 96 + 96 + 24 + 48 = 288 a day with working push.
        assertEquals(144, working.wakesPerDay)
        assertEquals(360, broken.wakesPerDay)
    }

    @Test fun withTheNotesAppInstalledOnlyItPullsNotes() = runBlocking {
        val fake = graphs.closeLast(object : AutoCloseable {
            val f = FakeNotesServer().start()
            override fun close() = f.close()
        }).f
        val g = signedInGraph(fake.server.url("/").toString().trimEnd('/'))
        g.setForeground(true)  // no notification polls
        g.notesPullHere = { false }
        assertTrue(backgroundSync(g))
        assertTrue(fake.log.map { "${it.method} ${it.path}" }.toString(), fake.log.none { it.method == "GET" && it.path.startsWith("notes") })
        g.notesPullHere = { true }
        assertTrue(backgroundSync(g))
        assertTrue(fake.log.any { it.method == "GET" && it.path.startsWith("notes") })
        Unit
    }
}
