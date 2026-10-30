package io.github.murtazanasir.mailr.data.phonecal

import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.util.catching
import kotlinx.coroutines.flow.Flow
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZoneOffset
import java.util.Locale

/**
 * Mailr's own events as this phone stores them (Room), read without the network. Hidden calendars
 * are included; the busy times the server keeps for phone calendars are not.
 */
interface MailrEvents {
    fun events(fromMs: Long, toMs: Long): Flow<List<CalendarEvent>>
    suspend fun eventsNow(fromMs: Long, toMs: Long): List<CalendarEvent>
}

/**
 * A phone event that is also in Mailr's calendar is shown and counted once, from Mailr's calendar
 * (docs/design/device-calendars.md, "Events in both calendars"). Exchange through the Gmail app keeps
 * no answer of the owner's (no attendee row, selfAttendeeStatus 0, no status) and leaves a declined
 * meeting BUSY or TENTATIVE; the owner answers in Mailr, so Mailr's copy holds the declined or
 * cancelled state (its `my_status` and `status`) and is already shown and on the server.
 *
 * A phone instance matches a Mailr event when both are timed with the same start and end, or both
 * all-day on the same dates, and their titles match after [normTitle]: equal, or one starts with the
 * other (Exchange sometimes shortens a title). The rule ([keep]) decides both what the calendar views
 * show and what is sent, so the two cannot disagree.
 */
object PhoneMatch {
    /** Prefixes mail and calendar programs put before a title, case-folded; removed again and again. */
    private val PREFIXES = listOf("canceled:", "cancelled:", "declined:", "accepted:", "tentative:", "updated:", "fwd:", "fw:", "re:")
    private val SPACE = Regex("\\s+")
    private const val TRAILING = ".,;:!?-–—"
    private const val DAY_MS = 86_400_000L
    /** The server's phone calendars ("phone:<key>", backend calendar/phonecal.py): busy times, never matched. */
    private const val SERVER_PHONE_PREFIX = "phone:"

    /** Case-folded, spaces collapsed, leading prefixes and trailing punctuation removed. */
    fun normTitle(title: String): String {
        var t = SPACE.replace(title.lowercase(Locale.ROOT), " ").trim()
        while (true) {
            val p = PREFIXES.firstOrNull { t.startsWith(it) } ?: break
            t = t.removePrefix(p).trimStart()
        }
        return t.trimEnd { it.isWhitespace() || it in TRAILING }
    }

    /** Equal after [normTitle], or one starts with the other. An empty title matches nothing. */
    fun titlesMatch(a: String, b: String): Boolean {
        val x = normTitle(a)
        val y = normTitle(b)
        if (x.isEmpty() || y.isEmpty()) return false
        return x == y || x.startsWith(y) || y.startsWith(x)
    }

    /** Exchange's title for a cancelled meeting: "Canceled: …" (or "Cancelled: …"). */
    fun cancelledTitle(title: String): Boolean {
        val t = title.trimStart()
        return t.startsWith("canceled:", ignoreCase = true) || t.startsWith("cancelled:", ignoreCase = true)
    }

    /** Mailr's events by time slot, with their titles. */
    class Index internal constructor(private val slots: Map<String, List<String>>) {
        val isEmpty: Boolean get() = slots.isEmpty()
        fun matches(i: PhoneInstance): Boolean = slots[slot(i)]?.any { titlesMatch(it, i.title) } == true
    }

    /** No Mailr events (Room not filled yet): nothing matches. */
    val NONE = Index(emptyMap())

    /** The phone's slot: timed by epoch ms; all-day by the UTC dates the provider keeps. */
    private fun slot(i: PhoneInstance): String =
        if (i.allDay) "d:${utcDate(i.begin)}:${utcDate(i.end)}" else "t:${i.begin}:${i.end}"

    private fun utcDate(ms: Long): LocalDate = Instant.ofEpochMilli(ms).atOffset(ZoneOffset.UTC).toLocalDate()

    /** Mailr's slot: timed by the instants of start and end; all-day by the dates (end exclusive, one day when missing). */
    private fun slot(e: CalendarEvent, zone: ZoneId): String? {
        if (e.allDay) {
            val start = catching { LocalDate.parse(e.start.take(10)) }.getOrNull() ?: return null
            val end = e.end?.let { catching { LocalDate.parse(it.take(10)) }.getOrNull() }?.takeIf { it > start } ?: start.plusDays(1)
            return "d:$start:$end"
        }
        val start = ReminderPlan.instant(e.start, zone) ?: return null
        val end = ReminderPlan.eventEnd(e, zone) ?: return null
        return "t:$start:$end"
    }

    /** Mailr's [events] (cancelled and declined ones too), without the server's busy times of phone calendars. */
    fun index(events: List<CalendarEvent>, zone: ZoneId): Index {
        if (events.isEmpty()) return NONE
        val slots = HashMap<String, MutableList<String>>()
        for (e in events) {
            if (e.calendar?.startsWith(SERVER_PHONE_PREFIX) == true) continue
            val s = slot(e, zone) ?: continue
            slots.getOrPut(s) { mutableListOf() } += e.summary
        }
        return Index(slots)
    }

    /**
     * Whether a phone instance is shown in the calendar views and may be sent as busy: not titled as
     * cancelled, and not also in Mailr's calendar ([mailr]). The one rule for display and sending.
     */
    fun keep(i: PhoneInstance, mailr: Index): Boolean = !cancelledTitle(i.title) && !mailr.matches(i)

    /** The window of Mailr events to read for phone instances in [fromMs, toMs): a day wider, for all-day dates in other zones. */
    fun readWindow(fromMs: Long, toMs: Long): Pair<Long, Long> = fromMs - DAY_MS to toMs + DAY_MS
}
