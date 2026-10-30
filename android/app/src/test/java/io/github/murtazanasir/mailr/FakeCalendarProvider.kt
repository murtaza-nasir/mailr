package io.github.murtazanasir.mailr

import android.Manifest
import android.app.Application
import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.provider.CalendarContract
import io.github.murtazanasir.mailr.data.phonecal.ProviderValues
import org.robolectric.Robolectric
import org.robolectric.Shadows.shadowOf
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Android's calendar provider for the tests: CalendarContract.Calendars and Instances
 * (instances/when/<begin>/<end>, with "calendar_id IN (?, …)"), answering by column name, so the
 * app's own queries run against it. [install] registers it under CalendarContract.AUTHORITY.
 */
class FakeCalendarProvider : ContentProvider() {
    data class Cal(val id: Long, val name: String, val account: String, val type: String, val color: Int)
    data class Inst(
        val eventId: Long, val calendarId: Long, val begin: Long, val end: Long, val allDay: Boolean = false,
        val title: String = "Event $eventId", val location: String? = null,
        val availability: Int = ProviderValues.AVAILABILITY_BUSY, val status: Int? = 1, val self: Int? = null,
    )

    companion object {
        val calendars = CopyOnWriteArrayList<Cal>()
        val instances = CopyOnWriteArrayList<Inst>()
        /** Every URI queried, for "nothing was read" checks. */
        val queried = CopyOnWriteArrayList<Uri>()

        fun install(app: Application, granted: Boolean = true) {
            calendars.clear(); instances.clear(); queried.clear()
            Robolectric.setupContentProvider(FakeCalendarProvider::class.java, CalendarContract.AUTHORITY)
            if (granted) shadowOf(app).grantPermissions(Manifest.permission.READ_CALENDAR)
            else shadowOf(app).denyPermissions(Manifest.permission.READ_CALENDAR)
        }
    }

    override fun onCreate(): Boolean = true

    override fun query(uri: Uri, projection: Array<out String>?, selection: String?, args: Array<out String>?, sort: String?): Cursor {
        queried += uri
        val cols = projection ?: emptyArray()
        val c = MatrixCursor(cols)
        val seg = uri.pathSegments
        when (seg.firstOrNull()) {
            "calendars" -> calendars.forEach { cal ->
                c.addRow(cols.map { col ->
                    when (col) {
                        CalendarContract.Calendars._ID -> cal.id
                        CalendarContract.Calendars.CALENDAR_DISPLAY_NAME -> cal.name
                        CalendarContract.Calendars.ACCOUNT_NAME -> cal.account
                        CalendarContract.Calendars.ACCOUNT_TYPE -> cal.type
                        CalendarContract.Calendars.CALENDAR_COLOR -> cal.color
                        else -> null
                    }
                })
            }
            "instances" -> {
                val from = seg[2].toLong()
                val to = seg[3].toLong()
                val ids = args?.map { it.toLong() }?.toSet()
                instances.filter { (ids == null || it.calendarId in ids) && it.end > from && it.begin < to }.forEach { i ->
                    c.addRow(cols.map { col ->
                        when (col) {
                            CalendarContract.Instances.EVENT_ID -> i.eventId
                            CalendarContract.Instances.CALENDAR_ID -> i.calendarId
                            CalendarContract.Instances.BEGIN -> i.begin
                            CalendarContract.Instances.END -> i.end
                            CalendarContract.Instances.ALL_DAY -> if (i.allDay) 1 else 0
                            CalendarContract.Instances.TITLE -> i.title
                            CalendarContract.Instances.EVENT_LOCATION -> i.location
                            CalendarContract.Instances.AVAILABILITY -> i.availability
                            CalendarContract.Instances.STATUS -> i.status
                            CalendarContract.Instances.SELF_ATTENDEE_STATUS -> i.self
                            else -> null
                        }
                    })
                }
            }
        }
        return c
    }

    override fun getType(uri: Uri): String? = null
    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, selection: String?, args: Array<out String>?): Int = 0
    override fun update(uri: Uri, values: ContentValues?, selection: String?, args: Array<out String>?): Int = 0
}
