package io.github.murtazanasir.mailr.data.sync

import io.github.murtazanasir.mailr.data.push.PushInfo

/**
 * How often the phone wakes in the background: the calendar sync and
 * the reminder watchdog run every 15 minutes only while push does not work; with working push they
 * run about hourly, as a safety net. Live events and push messages trigger the real syncs.
 *
 * "Working push" is a registered UnifiedPush target ([PushInfo.healthy]) that has delivered a
 * message within [RECENT_PUSH_MS]. A target that has gone quiet for longer is treated as broken
 * (a distributor killed by the phone's battery saver delivers nothing and reports nothing).
 */
object BackgroundSchedule {
    const val FAST_MINUTES = 15
    const val SAFETY_NET_MINUTES = 60
    /** Three days: a quiet weekend without mail does not count as broken push. */
    const val RECENT_PUSH_MS = 72L * 60 * 60 * 1000

    fun pushWorking(info: PushInfo, now: Long): Boolean =
        info.healthy && info.lastMessageAt?.let { now - it in 0 until RECENT_PUSH_MS } == true

    /** The calendar sync's and the reminder watchdog's period. */
    fun calendarMinutes(info: PushInfo, now: Long): Int = if (pushWorking(info, now)) SAFETY_NET_MINUTES else FAST_MINUTES

    /** The periodic jobs of both apps and their periods in minutes, for counting wakes. */
    data class Plan(val jobs: Map<String, Int>) {
        /** Scheduled wakes a day: each periodic job runs 24 h / period times. */
        val wakesPerDay: Int get() = jobs.values.sumOf { 24 * 60 / it }
    }

    /**
     * What this version schedules: mail sync ([PushRegistrar.pollMinutes]: 60 while the target is
     * connected), calendar sync and reminder watchdog (above), weather hourly while an Agenda widget
     * shows it, and the Notes app's own 30-minute sync when it is installed.
     */
    fun plan(info: PushInfo, now: Long, weather: Boolean, notesApp: Boolean): Plan = Plan(buildMap {
        put("sync", if (info.healthy) SAFETY_NET_MINUTES else FAST_MINUTES)
        put("cal-sync", calendarMinutes(info, now))
        put("reminder-tick", calendarMinutes(info, now))
        if (weather) put("agenda-weather", 60)
        if (notesApp) put("notes-app-sync", 30)
    })
}
