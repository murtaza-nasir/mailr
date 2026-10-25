package io.github.murtazanasir.mailr

import android.app.Application
import io.github.murtazanasir.mailr.data.api.RepeatSettings
import io.github.murtazanasir.mailr.data.reminders.AlertPrefs
import io.github.murtazanasir.mailr.data.reminders.RemindAgain
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * ACT reminder.repeat: Remind again is one setting on the server (`repeat` in /reminders/settings)
 * for the phone and the web. The phone still repeats on its own alarm with the server's values.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class RemindAgainServerTest {
    private lateinit var fake: FakeCalendarServer

    @Before fun setUp() { fake = FakeCalendarServer().start() }
    @After fun tearDown() = fake.stop()

    @Test fun theServerChoiceWinsAndAnOldServerKeepsThePhones() {
        val device = AlertPrefs(remindAgain = 5)
        assertEquals(device, RemindAgain.effective(device, null))
        val on = RemindAgain.effective(device, RepeatSettings(enabled = true, everyMin = 15, max = 2))
        assertEquals(15, on.remindAgain)
        assertEquals(2, on.remindMax)
        assertEquals(0, RemindAgain.effective(device, RepeatSettings(enabled = false)).remindAgain)
    }

    @Test fun aPhoneChoiceMovesUpOnlyWhileTheServerIsOff() {
        assertEquals(10, RemindAgain.migration(10, RepeatSettings(enabled = false)))
        assertNull(RemindAgain.migration(10, RepeatSettings(enabled = true, everyMin = 5)))
        assertNull(RemindAgain.migration(0, RepeatSettings(enabled = false)))
        assertNull(RemindAgain.migration(10, null))
    }

    @Test fun savingSendsOnlyWhatChanged() = runTest {
        fake.on("PUT", "reminders/settings") { _, _ ->
            fake.json("""{"events":true,"tasks":true,"repeat":{"enabled":true,"every_min":10,"max":2}}""")
        }
        fake.repo.setRemindAgain(10)
        val on = fake.body(fake.sent("PUT", "reminders/settings").last())["repeat"]!!.jsonObject
        assertEquals("true", on["enabled"]!!.jsonPrimitive.content)
        assertEquals("10", on["every_min"]!!.jsonPrimitive.content)
        assertFalse("a limit chosen on the web stays", on.containsKey("max"))
        assertEquals(RepeatSettings(enabled = true, everyMin = 10, max = 2), fake.repo.reminderSettings.value?.repeat)

        fake.repo.setRemindAgain(0)
        val off = fake.body(fake.sent("PUT", "reminders/settings").last())["repeat"]!!.jsonObject
        assertEquals(setOf("enabled"), off.keys)
        assertEquals("false", off["enabled"]!!.jsonPrimitive.content)
    }
}
