package io.github.murtazanasir.mailr.data.phonecal

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.database.Cursor
import android.provider.CalendarContract
import androidx.core.content.ContextCompat

/** The phone's calendars and their occurrences; tests use a fake provider or a fake source. */
interface PhoneCalendarSource {
    fun granted(): Boolean
    fun calendars(): List<PhoneCalendar>
    /** Occurrences of [calendarIds] that overlap [fromMs, toMs). */
    fun instances(calendarIds: Set<Long>, fromMs: Long, toMs: Long): List<PhoneInstance>
}

/**
 * Android's calendar provider (CalendarContract), read only. Without READ_CALENDAR every read is
 * empty. Calendars of accounts that belong to Mailr are left out, in case Mailr ever writes to the
 * provider.
 */
class ProviderCalendarSource(private val context: Context) : PhoneCalendarSource {
    override fun granted(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CALENDAR) == PackageManager.PERMISSION_GRANTED

    override fun calendars(): List<PhoneCalendar> {
        if (!granted()) return emptyList()
        val cols = arrayOf(CalendarContract.Calendars._ID, CalendarContract.Calendars.CALENDAR_DISPLAY_NAME,
            CalendarContract.Calendars.ACCOUNT_NAME, CalendarContract.Calendars.ACCOUNT_TYPE, CalendarContract.Calendars.CALENDAR_COLOR)
        val out = mutableListOf<PhoneCalendar>()
        query(CalendarContract.Calendars.CONTENT_URI, cols, null, null) { c ->
            val type = c.str(3)
            if (ownAccount(type)) return@query
            out += PhoneCalendar(c.getLong(0), c.str(1), c.str(2), type, if (c.isNull(4)) 0 else c.getInt(4))
        }
        return out.sortedWith(compareBy({ it.accountName.lowercase() }, { it.name.lowercase() }, { it.id }))
    }

    override fun instances(calendarIds: Set<Long>, fromMs: Long, toMs: Long): List<PhoneInstance> {
        if (calendarIds.isEmpty() || !granted() || toMs <= fromMs) return emptyList()
        val uri = CalendarContract.Instances.CONTENT_URI.buildUpon().also {
            ContentUris.appendId(it, fromMs)
            ContentUris.appendId(it, toMs)
        }.build()
        val cols = arrayOf(CalendarContract.Instances.EVENT_ID, CalendarContract.Instances.CALENDAR_ID, CalendarContract.Instances.BEGIN,
            CalendarContract.Instances.END, CalendarContract.Instances.ALL_DAY, CalendarContract.Instances.TITLE,
            CalendarContract.Instances.EVENT_LOCATION, CalendarContract.Instances.AVAILABILITY, CalendarContract.Instances.STATUS,
            CalendarContract.Instances.SELF_ATTENDEE_STATUS)
        val sel = "${CalendarContract.Instances.CALENDAR_ID} IN (${calendarIds.joinToString(",") { "?" }})"
        val out = mutableListOf<PhoneInstance>()
        query(uri, cols, sel, calendarIds.map { it.toString() }.toTypedArray()) { c ->
            out += PhoneInstance(
                eventId = c.getLong(0), calendarId = c.getLong(1), begin = c.getLong(2), end = c.getLong(3),
                allDay = !c.isNull(4) && c.getInt(4) != 0, title = c.str(5), location = c.getString(6)?.takeIf { it.isNotBlank() },
                availability = if (c.isNull(7)) ProviderValues.AVAILABILITY_BUSY else c.getInt(7),
                status = if (c.isNull(8)) null else c.getInt(8),
                selfStatus = if (c.isNull(9)) null else c.getInt(9),
            )
        }
        // The provider answers by calendar too; a fake one may not, so the ids are checked here.
        return out.filter { it.calendarId in calendarIds && it.end > fromMs && it.begin < toMs }
    }

    private fun ownAccount(type: String): Boolean = type.startsWith(OWN_PACKAGE_PREFIX) || type == context.packageName

    private fun Cursor.str(i: Int): String = if (isNull(i)) "" else getString(i).orEmpty()

    private inline fun query(uri: android.net.Uri, cols: Array<String>, sel: String?, args: Array<String>?, row: (Cursor) -> Unit) {
        val c = try {
            context.contentResolver.query(uri, cols, sel, args, null)
        } catch (_: SecurityException) {
            null // the permission was taken back between the check and the read
        } ?: return
        c.use { while (it.moveToNext()) row(it) }
    }

    companion object {
        private const val OWN_PACKAGE_PREFIX = "io.github.murtazanasir.mailr"
    }
}
