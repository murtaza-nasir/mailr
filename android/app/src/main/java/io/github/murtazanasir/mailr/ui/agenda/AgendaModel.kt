package io.github.murtazanasir.mailr.ui.agenda

import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.cal.SmartList
import io.github.murtazanasir.mailr.data.cal.TaskQueries
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import io.github.murtazanasir.mailr.ui.calendar.weekStart
import io.github.murtazanasir.mailr.ui.today.TodayModel
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.temporal.ChronoUnit
import java.time.temporal.WeekFields
import java.util.Locale

/**
 * What the Agenda widget shows: the next few days of events,
 * grouped by day, the open tasks that are due in that window or overdue, and the weather the phone
 * fetched last. Built from the phone's own copy of the calendar and tasks, so it works offline.
 *
 * The four styles (AgendaStyle, 7 Oct 2026) read different parts of it: the glass timeline reads
 * [timeline] and [nowMinute]; paper & rail reads [days] with [earlier]; the week strip reads [week];
 * numeral duotone reads [next], [upcoming] and [hourly].
 */
data class AgendaModel(
    /** "7". */
    val dayNumber: String,
    /** "Wednesday". */
    val weekday: String,
    /** "October". */
    val month: String,
    /** "Wed", "Oct": the narrow widget's header. */
    val weekdayShort: String,
    val monthShort: String,
    val days: List<Day>,
    val tasks: List<Task>,
    val weather: Weather? = null,
    /** False: the user is signed out, or the calendar is not connected. */
    val ready: Boolean = true,
    val undo: TodayModel.Undo? = null,
    /** "Wednesday, 7 October" and "Wed, 7 Oct" (paper & rail). */
    val dateLong: String = "",
    val dateShort: String = "",
    /** "Wed 7 Oct" (the narrow week strip). */
    val dateNarrow: String = "",
    /** "7 October" (the glass timeline). */
    val dayMonth: String = "",
    /** The week of the year in the phone's language rules (paper & rail). */
    val weekNumber: Int = 0,
    /** Minutes since today's midnight: where the now-line sits. */
    val nowMinute: Int = 0,
    /** Today's timed events that have ended, oldest first (shown faded). */
    val earlier: List<Event> = emptyList(),
    /** Today's timed events, ended ones too, by start: the glass timeline. */
    val timeline: List<Timed> = emptyList(),
    /** This week, from the phone's first day of the week: the week strip. */
    val week: List<WeekDay> = emptyList(),
    /** The next timed event (or the one under way): numeral duotone's card. */
    val next: Next? = null,
    /** The timed events after [next], with their day when it is not today. */
    val upcoming: List<Upcoming> = emptyList(),
    /** The hourly forecast strip: the next full hour, then every three hours. */
    val hourly: List<HourSlot> = emptyList(),
    /** Weather is on but no place is known yet: the header shows "Set weather". */
    val weatherHint: Boolean = false,
    val h24: Boolean = false,
) {
    data class Day(val date: LocalDate, val label: String, val today: Boolean, val events: List<Event>) {
        /** "Today", then "Thursday 8" (paper & rail). */
        val longLabel: String get() = if (today) label else date.format(LocaleFormats.date("EEEEd", "EEEE d"))
        /** "Today", then "Thu 8" (week strip). */
        val shortLabel: String get() = if (today) label else date.format(LocaleFormats.date("EEEd", "EEE d"))
    }

    /** [start] and [end] in epoch ms; [place] is the event's location; [past]: a timed event that has ended. */
    data class Event(val uid: String, val occurrence: String, val title: String, val time: String, val colour: String?,
                     val calendar: String?, val allDay: Boolean, val now: Boolean,
                     val start: Long = 0, val end: Long = 0, val place: String? = null, val past: Boolean = false,
                     val endTime: String = "")

    /** [day]: the day the task is due, or null when it is overdue or has started without a due date in the window. */
    data class Task(val uid: String, val title: String, val note: String?, val overdue: Boolean, val day: LocalDate?)

    /** A cached reading: [temp], [high] and [low] are already rounded in the chosen unit; [hours] is the hourly forecast. */
    data class Weather(val icon: WeatherIcon, val temp: Int, val high: Int, val low: Int, val place: String?,
                       val hours: List<Hour> = emptyList()) {
        val tempLine: String get() = AppText.get(R.string.agenda_degrees, temp)
        val rangeLine: String get() = AppText.get(R.string.agenda_high_low, high, low)
    }

    /** One hour of the forecast: its start in epoch ms, picture and rounded temperature. */
    data class Hour(val at: Long, val icon: WeatherIcon, val temp: Int)

    /** One slot of the hourly strip: "15" (or "3 PM"), the picture, the temperature. */
    data class HourSlot(val label: String, val icon: WeatherIcon, val temp: Int)

    /** An event on the timeline: minutes from today's midnight, clamped to the day. */
    data class Timed(val event: Event, val startMin: Int, val endMin: Int) {
        val past: Boolean get() = event.past
    }

    /** A day of the week strip and up to three event colours (calendar, colour) for its dots. */
    data class WeekDay(val date: LocalDate, val letter: String, val number: String, val today: Boolean, val past: Boolean,
                       val dots: List<Pair<String?, String?>>, val label: String)

    /**
     * The next event: [minutes] until it starts when that is within the hour (the countdown), null
     * otherwise; [underWay] when it has started; [day] "Tomorrow", "Thu" or "Mon 19 Oct" when it is not
     * today (relativeDay); [endTime] "15:00".
     */
    data class Next(val event: Event, val minutes: Int?, val underWay: Boolean, val durationMin: Int, val day: String?, val endTime: String)

    /** [day]: null today, else as relativeDay names it. */
    data class Upcoming(val event: Event, val day: String?)

    val eventCount: Int get() = days.sumOf { it.events.size }
    val empty: Boolean get() = eventCount == 0 && tasks.isEmpty()
    /** Today's events, ended ones too ("4 events today"). */
    val eventsToday: Int get() = (days.firstOrNull()?.events?.size ?: 0) + earlier.size

    /** Tasks shown with a day of their own (single column): overdue and undated ones belong to today. */
    fun tasksOn(day: Day): List<Task> = tasks.filter { if (day.today) it.day == null || it.day == day.date else it.day == day.date }

    companion object {
        const val DEFAULT_DAYS = 5
        const val MAX_TASKS = 12
        /** The countdown ("in 25 min") shows for an event that starts within this many minutes. */
        const val COUNTDOWN_MIN = 60

        fun signedOut(now: ZonedDateTime, weather: Weather? = null, weatherHint: Boolean = false): AgendaModel {
            val d = now.toLocalDate()
            return AgendaModel(d.dayOfMonth.toString(), weekday(d), month(d), weekdayShort(d), monthShort(d), emptyList(), emptyList(), weather,
                ready = false, dateLong = dateLong(d), dateShort = dateShort(d), dateNarrow = dateNarrow(d), dayMonth = dayMonth(d), weekNumber = weekNumber(d),
                nowMinute = now.hour * 60 + now.minute, weatherHint = weatherHint,
                week = (0L until 7L).map { i -> weekStart(d).plusDays(i) }.map { w ->
                    WeekDay(w, w.format(LocaleFormats.date("EEEEE", "EEEEE")), w.dayOfMonth.toString(), w == d, w.isBefore(d), emptyList(), dayLabel(w, d))
                })
        }

        fun weekday(d: LocalDate): String = d.format(LocaleFormats.date("EEEE", "EEEE"))
        fun month(d: LocalDate): String = d.format(LocaleFormats.date("LLLL", "LLLL"))
        fun weekdayShort(d: LocalDate): String = d.format(LocaleFormats.date("EEE", "EEE"))
        fun monthShort(d: LocalDate): String = d.format(LocaleFormats.date("LLL", "LLL"))
        fun dateLong(d: LocalDate): String = d.format(LocaleFormats.date("EEEEdMMMM", "EEEE, d MMMM"))
        fun dateShort(d: LocalDate): String = d.format(LocaleFormats.date("EEEdMMM", "EEE, d MMM"))
        fun dateNarrow(d: LocalDate): String = d.format(LocaleFormats.date("EEEdMMM", "EEE d MMM"))
        fun dayMonth(d: LocalDate): String = d.format(LocaleFormats.date("dMMMM", "d MMMM"))

        /** The week of the year by the phone's language rules (ISO in most of Europe, Sunday-first in the US). */
        fun weekNumber(d: LocalDate, locale: Locale = Locale.getDefault()): Int = d.get(WeekFields.of(locale).weekOfWeekBasedYear())

        /** "Today", "Tomorrow", then "Fri 9 Oct". */
        fun dayLabel(d: LocalDate, today: LocalDate): String = when (d) {
            today -> AppText.get(R.string.agenda_today)
            today.plusDays(1) -> AppText.get(R.string.agenda_tomorrow)
            else -> d.format(LocaleFormats.date("EEEdMMM", "EEE d MMM"))
        }

        /**
         * A later day as the event and task rows name it (with days up
         * to two weeks ahead, "Mon 17:35" alone could be this Monday or the next): "Today", "Tomorrow", the
         * short weekday for the rest of the coming week ("Mon", two to six days ahead), then the weekday
         * with its date ("Mon 19 Oct", in the phone's language).
         */
        fun relativeDay(d: LocalDate, today: LocalDate): String = when (val ahead = ChronoUnit.DAYS.between(today, d)) {
            0L -> AppText.get(R.string.agenda_today)
            1L -> AppText.get(R.string.agenda_tomorrow)
            in 2L..6L -> weekdayShort(d)
            else -> d.format(LocaleFormats.date("EEEdMMM", "EEE d MMM"))
        }

        /** The first day the widget needs events for: the start of this week (the strip's dots). */
        fun from(today: LocalDate): LocalDate = minOf(weekStart(today), today)

        /** The day after the last one it needs: the end of the listed days or of this week. */
        fun until(today: LocalDate, days: Int): LocalDate = maxOf(today.plusDays(days.coerceIn(1, 14).toLong()), weekStart(today).plusDays(7))

        /**
         * [days] days from today: each day's events (all-day first, then by start; cancelled and declined
         * ones left out; today's events that have ended left out of [days] and kept in [earlier]), and the
         * open top-level tasks that are overdue, due today or started, or due on one of the shown days.
         * Overdue first, then by due. Events earlier in the week only colour the week strip.
         */
        fun build(events: List<CalendarEvent>, tasks: List<TaskItem>, now: ZonedDateTime, days: Int = DEFAULT_DAYS, h24: Boolean = false,
                  weather: Weather? = null, undo: TodayModel.Undo? = null, weatherHint: Boolean = false): AgendaModel {
            val zone = now.zone
            val nowMs = now.toInstant().toEpochMilli()
            val today = now.toLocalDate()
            val live = events.filter { !it.cancelled && !it.myStatus.equals("DECLINED", ignoreCase = true) }
                .mapNotNull { e ->
                    val start = ReminderPlan.instant(e.start, zone) ?: return@mapNotNull null
                    Triple(e, start, ReminderPlan.eventEnd(e, zone) ?: start)
                }
            fun bounds(d: LocalDate) = d.atStartOfDay(zone).toInstant().toEpochMilli() to d.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
            fun on(d: LocalDate) = bounds(d).let { (from, to) ->
                live.asSequence().filter { (_, s, end) -> s < to && (end > from || (end == s && s >= from)) }
                    .sortedWith(compareBy({ !it.first.allDay }, { it.second })).distinctBy { it.first.key }
            }
            fun event(e: CalendarEvent, s: Long, end: Long, d: LocalDate): Event {
                val (from, to) = bounds(d)
                val allDay = e.allDay || (s < from && end >= to)
                return Event(e.uid, e.occurrence, e.summary.ifBlank { AppText.get(R.string.today_no_title) },
                    when {
                        allDay -> AppText.get(R.string.today_all_day)
                        s < from -> AppText.get(R.string.agenda_until, TodayModel.time(end, zone, h24))
                        else -> TodayModel.time(s, zone, h24)
                    },
                    e.calendarColor ?: e.color, e.calendar, allDay, now = d == today && !allDay && s <= nowMs && end > nowMs,
                    start = s, end = end, place = e.location?.takeIf { it.isNotBlank() },
                    past = !allDay && end <= nowMs && !(end == s && s >= nowMs), endTime = TodayModel.time(end, zone, h24))
            }
            val todayAll = on(today).map { (e, s, end) -> event(e, s, end, today) }.toList()
            val dayList = (0 until days.coerceIn(1, 14)).map { i ->
                val d = today.plusDays(i.toLong())
                val evs = if (i == 0) todayAll.filter { !it.past } else on(d).map { (e, s, end) -> event(e, s, end, d) }.toList()
                Day(d, dayLabel(d, today), i == 0, evs)
            }
            val dayFrom = bounds(today).first
            val timeline = todayAll.filter { !it.allDay }.map { e ->
                Timed(e, ((e.start - dayFrom) / 60_000).toInt().coerceIn(0, 24 * 60), ((e.end - dayFrom) / 60_000).toInt().coerceIn(0, 24 * 60))
            }
            val ws = weekStart(today)
            val week = (0L until 7L).map { i ->
                val d = ws.plusDays(i)
                val dots = on(d).map { it.first.calendar to (it.first.calendarColor ?: it.first.color) }.distinct().take(3).toList()
                WeekDay(d, d.format(LocaleFormats.date("EEEEE", "EEEEE")), d.dayOfMonth.toString(), d == today, d.isBefore(today), dots,
                    dayLabel(d, today))
            }
            // Numeral duotone: the next timed event (or the one under way), then the timed events after it.
            val timedAhead = dayList.flatMap { day -> day.events.filter { !it.allDay && it.end > nowMs }.map { it to day } }
                .distinctBy { it.first.uid to it.first.occurrence }
            val next = timedAhead.firstOrNull()?.let { (e, day) ->
                val underWay = e.start <= nowMs
                val mins = ((e.start - nowMs + 59_999) / 60_000).toInt()
                Next(e, if (!underWay && mins <= COUNTDOWN_MIN) mins else null, underWay, ((e.end - e.start) / 60_000).toInt(),
                    if (day.today) null else relativeDay(day.date, today), TodayModel.time(e.end, zone, h24))
            }
            val upcoming = timedAhead.drop(1).map { (e, day) -> Upcoming(e, if (day.today) null else relativeDay(day.date, today)) }
            val last = today.plusDays(dayList.size.toLong() - 1)
            val tk = tasks.asSequence()
                .filter { TaskQueries.open(it) && it.parent == null && !TaskQueries.deferred(it, nowMs, zone) }
                .mapNotNull { t ->
                    val overdue = TaskQueries.inSmart(t, SmartList.OVERDUE, nowMs, zone)
                    val dueDay = t.due?.let { ReminderPlan.instant(it, zone) }?.let { Instant.ofEpochMilli(it).atZone(zone).toLocalDate() }
                    when {
                        overdue -> Triple(t, true, null as LocalDate?)
                        dueDay != null && !dueDay.isBefore(today) && !dueDay.isAfter(last) -> Triple(t, false, dueDay)
                        TaskQueries.inSmart(t, SmartList.TODAY, nowMs, zone) -> Triple(t, false, null)
                        else -> null
                    }
                }
                .sortedWith(compareBy({ !it.second }, { TodayModel.dueOrder(it.first, zone) }))
                .take(MAX_TASKS)
                .map { (t, overdue, day) -> Task(t.uid, t.summary.ifBlank { AppText.get(R.string.today_no_title) }, note(t, overdue, today, zone, h24), overdue, day) }
                .toList()
            return AgendaModel(today.dayOfMonth.toString(), weekday(today), month(today), weekdayShort(today), monthShort(today), dayList, tk,
                weather, undo = undo, dateLong = dateLong(today), dateShort = dateShort(today), dateNarrow = dateNarrow(today), dayMonth = dayMonth(today),
                weekNumber = weekNumber(today), nowMinute = now.hour * 60 + now.minute,
                earlier = todayAll.filter { it.past }, timeline = timeline, week = week, next = next, upcoming = upcoming,
                hourly = weather?.let { hourSlots(it.hours, now, h24) }.orEmpty(), weatherHint = weatherHint, h24 = h24)
        }

        /**
         * The hourly strip: the next full hour, then every [step] hours, [count] slots, each from the
         * forecast hour that starts then (a slot without one is left out).
         */
        fun hourSlots(hours: List<Hour>, now: ZonedDateTime, h24: Boolean, count: Int = 4, step: Long = 3): List<HourSlot> {
            if (hours.isEmpty()) return emptyList()
            val first = now.withMinute(0).withSecond(0).withNano(0).plusHours(1)
            return (0 until count).mapNotNull { i ->
                val at = first.plusHours(i * step)
                val ms = at.toInstant().toEpochMilli()
                val h = hours.firstOrNull { kotlin.math.abs(it.at - ms) < 30 * 60_000 } ?: return@mapNotNull null
                HourSlot(if (h24) "%02d".format(at.hour) else at.format(LocaleFormats.date("ha", "h a")), h.icon, h.temp)
            }
        }

        /** The short note at the end of a task row: "Fri 2 Oct" (overdue), "3 PM", "Tomorrow", "Thu", "Mon 19 Oct", or null for today with no time. */
        private fun note(t: TaskItem, overdue: Boolean, today: LocalDate, zone: ZoneId, h24: Boolean): String? {
            val due = t.due ?: return null
            val ms = ReminderPlan.instant(due, zone) ?: return null
            val day = Instant.ofEpochMilli(ms).atZone(zone).toLocalDate()
            val dateOnly = ReminderPlan.isDateOnly(due)
            return when {
                overdue && day.isBefore(today) -> day.format(LocaleFormats.date("EEEdMMM", "EEE d MMM"))
                day == today -> if (dateOnly) null else TodayModel.time(ms, zone, h24)
                else -> relativeDay(day, today)
            }
        }
    }
}

/** The weather picture: WMO weather codes (Open-Meteo's `weather_code`) in eight groups, day and night. */
enum class WeatherIcon {
    CLEAR_DAY, CLEAR_NIGHT, PARTLY_DAY, PARTLY_NIGHT, CLOUDY, FOG, RAIN, SNOW, STORM;

    companion object {
        fun of(code: Int, day: Boolean): WeatherIcon = when (code) {
            0, 1 -> if (day) CLEAR_DAY else CLEAR_NIGHT
            2 -> if (day) PARTLY_DAY else PARTLY_NIGHT
            3 -> CLOUDY
            45, 48 -> FOG
            in 51..67, in 80..82 -> RAIN
            in 71..77, 85, 86 -> SNOW
            95, 96, 99 -> STORM
            else -> CLOUDY
        }
    }
}
