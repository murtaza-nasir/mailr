package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.Invite
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.cal.Zones
import io.github.murtazanasir.mailr.ui.calendar.whenLineZones
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import android.app.Application
import java.time.ZoneId

/**
 * Zone names from Outlook (9 Oct 2026): "Central Standard Time" made ZoneId.of throw. The server
 * sends IANA ids now; an older server or a stored row may not, so every zone the app reads goes
 * through Zones (a Windows → IANA table generated from the server's, tools/android_windows_zones.py).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ZonesTest {
    @Test fun windowsNamesMapToIana() {
        assertEquals("America/Chicago", Zones.iana("Central Standard Time"))
        assertEquals("America/New_York", Zones.iana("Eastern Standard Time"))
        assertEquals("America/Los_Angeles", Zones.iana("Pacific Standard Time"))
        assertEquals("Europe/Berlin", Zones.iana("W. Europe Standard Time"))
        assertEquals("Asia/Calcutta", Zones.iana("India Standard Time"))
        assertEquals(ZoneId.of("America/Chicago"), Zones.of("Central Standard Time"))
    }

    @Test fun ianaStaysAndUnknownIsNull() {
        assertEquals("Europe/London", Zones.iana("Europe/London"))
        assertEquals("Europe/London", Zones.iana("/mozilla.org/20050126_1/Europe/London"))
        assertEquals("UTC", Zones.iana("Etc/UTC"))
        assertEquals("UTC", Zones.iana("Z"))
        assertNull(Zones.iana("Customized Time Zone"))
        assertNull(Zones.iana("UTC-05:00"))
        assertNull(Zones.iana(""))
        assertNull(Zones.of(null))
        assertEquals(ZoneId.of("Asia/Tokyo"), Zones.orDefault("Nowhere", ZoneId.of("Asia/Tokyo")))
    }

    @Test fun eventsAndInvitesDecodeToIana() {
        val e = MailrJson.decodeFromString(CalendarEvent.serializer(),
            """{"uid":"u1","summary":"Budget","start":"2026-10-15T15:00:00+00:00","end":"2026-10-15T16:30:00+00:00","tz":"Central Standard Time","end_tz":"Bogus Zone"}""")
        assertEquals("America/Chicago", e.tz)
        assertNull(e.endTz)
        val none = MailrJson.decodeFromString(CalendarEvent.serializer(), """{"uid":"u2","start":"2026-10-15","tz":null}""")
        assertNull(none.tz)
        val inv = MailrJson.decodeFromString(Invite.serializer(), """{"uid":"i1","tz":"W. Europe Standard Time"}""")
        assertEquals("Europe/Berlin", inv.tz)
    }

    @Test fun anOldRowWithARawNameStillShows() {
        // A row stored before the fix keeps the raw name; the when line falls back without throwing.
        val raw = CalendarEvent(uid = "u3", summary = "Old", start = "2026-10-15T15:00:00+00:00", end = "2026-10-15T16:00:00+00:00", tz = "Central Standard Time")
        val line = whenLineZones(raw, ZoneId.of("Europe/London"))
        assert(line.isNotBlank())
    }
}
