package io.github.murtazanasir.mailr.data.phonecal

import io.github.murtazanasir.mailr.data.api.MailrJson
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.nullable
import kotlinx.serialization.builtins.serializer
import java.security.MessageDigest
import java.time.Instant
import java.time.temporal.ChronoUnit

/** A calendar in Android's calendar provider (CalendarContract.Calendars). */
data class PhoneCalendar(
    val id: Long,
    val name: String,
    val accountName: String,
    val accountType: String,
    /** ARGB, as the provider keeps it. */
    val color: Int,
) {
    /** "#rrggbb", the form the app and the server use. */
    val hex: String get() = "#%06x".format(color and 0xFFFFFF)

    /** The name the server shows: the display name, with the account when it says something more. */
    val label: String get() = if (accountName.isBlank() || accountName == name) name else "$name ($accountName)"
}

/** One occurrence of a phone event in a window (CalendarContract.Instances). */
data class PhoneInstance(
    val eventId: Long,
    val calendarId: Long,
    /** Epoch ms. An all-day instance starts and ends at UTC midnight, as the provider keeps it. */
    val begin: Long,
    val end: Long,
    val allDay: Boolean,
    val title: String,
    val location: String?,
    /** Events.AVAILABILITY_BUSY (0), _FREE (1) or _TENTATIVE (2). */
    val availability: Int,
    /** Events.STATUS_TENTATIVE (0), _CONFIRMED (1), _CANCELED (2), or null. */
    val status: Int?,
    /** Attendees.ATTENDEE_STATUS_*: the phone owner's answer, or null. */
    val selfStatus: Int?,
)

/**
 * Per phone calendar: nothing, shown in the app, shown and its busy times sent to the server, or
 * shown and its busy times sent with each event's title and place ("Show and send details").
 */
enum class PhoneCalMode { OFF, SHOW, SEND, DETAILS;
    val shown: Boolean get() = this != OFF
    /** Its busy times go to the server (SEND and DETAILS). */
    val sends: Boolean get() = this == SEND || this == DETAILS
}

/**
 * The body of PUT /api/v1/calendar/phone-calendars/{key}. These two classes are everything the
 * request can carry: a calendar's name, colour and device, the window, whether details are sent,
 * and per busy instance an opaque id, its start and end, whether it is all-day, and, with
 * [details] only, the event's title and place. No guests, notes, organizer or event id.
 *
 * A busy-only body ([details] false) cannot hold a title or a place: [PhoneBusy.entries] builds
 * its entries through [BusyEntry.busy], and the check below refuses any other.
 */
@Serializable
data class PhoneCalendarPut(
    val name: String,
    val color: String,
    val device: String,
    @SerialName("window_start") val windowStart: String,
    @SerialName("window_end") val windowEnd: String,
    /** True for "Show and send details"; false sends times only, and the server erases the titles and places it held. */
    val details: Boolean,
    val busy: List<BusyEntry>,
) {
    init {
        require(details || busy.all { it.title == null && it.location == null }) { "a busy-only body holds a title or a place" }
    }
}

/** One busy instance. [title] and [location] are null (left out of the JSON) unless the calendar sends details. */
@Serializable
data class BusyEntry(
    val id: String,
    val start: String,
    val end: String,
    @SerialName("all_day") val allDay: Boolean,
    val title: String? = null,
    val location: String? = null,
) {
    companion object {
        /** Times only: the entry of a "Show and send busy times" calendar. */
        fun busy(id: String, start: String, end: String, allDay: Boolean) = BusyEntry(id, start, end, allDay)

        /** Times with the title and place, each trimmed and cut to [PhoneBusy.MAX_TEXT] characters; an empty one is left out. */
        fun detailed(id: String, start: String, end: String, allDay: Boolean, title: String?, location: String?) =
            BusyEntry(id, start, end, allDay, PhoneBusy.text(title), PhoneBusy.text(location))
    }
}

/** The server's answer to the PUT. */
@Serializable
data class PhoneCalendarStored(val stored: Int = 0, @SerialName("synced_at") val syncedAt: String? = null)

/** The provider's numbers (CalendarContract), kept here so the rules read without Android. */
object ProviderValues {
    const val AVAILABILITY_BUSY = 0
    const val AVAILABILITY_FREE = 1
    const val AVAILABILITY_TENTATIVE = 2
    const val STATUS_CANCELED = 2
    const val ATTENDEE_STATUS_DECLINED = 2
}

/** The busy-times rules of docs/design/device-calendars.md (Phase 2, Android sync). */
object PhoneBusy {
    const val PAST_DAYS = 30L
    const val FUTURE_DAYS = 180L
    /** The server takes at most this many entries per calendar. */
    const val MAX_ENTRIES = 5000
    /** The server takes a title or a place of at most this many characters. */
    const val MAX_TEXT = 300

    fun sha256Hex(s: String): String =
        MessageDigest.getInstance("SHA-256").digest(s.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }

    /**
     * The calendar's key on the server: sha256 of the install id, account type, account name and
     * calendar id (one per line), the first 32 hex digits. The same phone calendar on the same
     * install always has the same key.
     */
    fun calendarKey(installId: String, accountType: String, accountName: String, calendarId: Long): String =
        sha256Hex(listOf(installId, accountType, accountName, calendarId.toString()).joinToString("\n")).take(32)

    /** A busy entry's id: sha256 of the key, the event id and the instance's begin, the first 24 hex digits. */
    fun busyId(key: String, eventId: Long, begin: Long): String =
        sha256Hex(listOf(key, eventId.toString(), begin.toString()).joinToString("\n")).take(24)

    /** Busy or tentative, not declined by the phone's owner, not cancelled. */
    fun counts(i: PhoneInstance): Boolean =
        (i.availability == ProviderValues.AVAILABILITY_BUSY || i.availability == ProviderValues.AVAILABILITY_TENTATIVE) &&
            i.status != ProviderValues.STATUS_CANCELED &&
            i.selfStatus != ProviderValues.ATTENDEE_STATUS_DECLINED

    /** UTC, whole seconds: "2026-10-30T17:00:00Z". */
    fun iso(ms: Long): String = Instant.ofEpochMilli(ms).truncatedTo(ChronoUnit.SECONDS).toString()

    /**
     * The busy entries of [instances] for the calendar [key]: only the ones that [counts] and that
     * [PhoneMatch.keep] keeps (not also in Mailr's calendar [mailr], not titled as cancelled), sorted
     * by start, one per id, at most [MAX_ENTRIES]. Without [details] only times leave this function;
     * with it, each entry's title and place too.
     */
    fun entries(key: String, instances: List<PhoneInstance>, mailr: PhoneMatch.Index = PhoneMatch.NONE,
                details: Boolean = false): List<BusyEntry> =
        instances.asSequence().filter(::counts).filter { PhoneMatch.keep(it, mailr) }.filter { it.end >= it.begin }
            .sortedWith(compareBy({ it.begin }, { it.end }, { it.eventId }))
            .map { i ->
                val id = busyId(key, i.eventId, i.begin)
                if (details) BusyEntry.detailed(id, iso(i.begin), iso(i.end), i.allDay, i.title, i.location)
                else BusyEntry.busy(id, iso(i.begin), iso(i.end), i.allDay)
            }
            .distinctBy { it.id }
            .take(MAX_ENTRIES)
            .toList()

    /** A title or place as sent: trimmed, at most [MAX_TEXT] characters (whole code points), null when empty. */
    fun text(s: String?): String? {
        val t = s?.trim().orEmpty()
        if (t.isEmpty()) return null
        if (t.codePointCount(0, t.length) <= MAX_TEXT) return t
        return t.substring(0, t.offsetByCodePoints(0, MAX_TEXT)).trimEnd()
    }

    /** The window around [nowMs]: 30 days back, 180 days ahead. */
    fun window(nowMs: Long): Pair<Long, Long> =
        nowMs - PAST_DAYS * 86_400_000L to nowMs + FUTURE_DAYS * 86_400_000L

    /** The snapshot of one calendar; [details] for "Show and send details", false for "Show and send busy times". */
    fun body(cal: PhoneCalendar, device: String, key: String, instances: List<PhoneInstance>, window: Pair<Long, Long>,
             mailr: PhoneMatch.Index = PhoneMatch.NONE, details: Boolean = false): PhoneCalendarPut =
        PhoneCalendarPut(cal.label, cal.hex, device, iso(window.first), iso(window.second), details,
            entries(key, instances, mailr, details))

    /**
     * The snapshot's hash: name, colour, device and busy entries, and with details the flag and each
     * entry's title and place. The window is left out, since it moves with the clock; an unchanged
     * hash means no request. A busy-only snapshot hashes as before details existed, so an update of
     * the app sends nothing again; a change of the choice between the two, or of a title or place,
     * changes the hash and the next run sends the snapshot. An entry left out because it is also in
     * Mailr's calendar changes the entries, so the hash too.
     */
    fun hash(b: PhoneCalendarPut): String =
        sha256Hex(buildString {
            append(b.name).append('\n').append(b.color).append('\n').append(b.device).append('\n')
            if (b.details) append("details\n")
            b.busy.forEach {
                append(it.id).append(' ').append(it.start).append(' ').append(it.end).append(' ').append(it.allDay)
                if (b.details) append(' ').append(MailrJson.encodeToString(String.serializer().nullable, it.title))
                    .append(' ').append(MailrJson.encodeToString(String.serializer().nullable, it.location))
                append('\n')
            }
        })
}
