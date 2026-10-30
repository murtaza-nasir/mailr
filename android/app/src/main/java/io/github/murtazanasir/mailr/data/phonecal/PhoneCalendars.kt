package io.github.murtazanasir.mailr.data.phonecal

import android.content.ContentUris
import android.content.Context
import android.content.Intent
import android.database.ContentObserver
import android.os.Handler
import android.os.Looper
import android.provider.CalendarContract
import android.util.Log
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.util.catching
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import java.io.IOException
import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset

/** Phone events in the app's calendar: "phone:<event id>" as uid, the instance's begin (ms) as occurrence. */
object PhoneEvents {
    const val UID_PREFIX = "phone:"
    const val CALENDAR_PREFIX = "phone:"

    fun isPhone(e: CalendarEvent): Boolean = e.uid.startsWith(UID_PREFIX)
    fun isPhoneKey(key: String): Boolean = key.startsWith(UID_PREFIX)

    fun eventId(e: CalendarEvent): Long? = if (isPhone(e)) e.uid.removePrefix(UID_PREFIX).toLongOrNull() else null

    /** A shown occurrence as the app's calendar draws it: read-only, in its calendar's colour, no reminders. */
    fun toEvent(i: PhoneInstance, cal: PhoneCalendar, zone: ZoneId): CalendarEvent {
        val start: String
        val end: String
        if (i.allDay) {
            // All-day instances sit at UTC midnight in the provider: their dates are the UTC dates.
            start = Instant.ofEpochMilli(i.begin).atOffset(ZoneOffset.UTC).toLocalDate().toString()
            end = Instant.ofEpochMilli(i.end).atOffset(ZoneOffset.UTC).toLocalDate().toString()
        } else {
            start = Instant.ofEpochMilli(i.begin).atZone(zone).toOffsetDateTime().toString()
            end = Instant.ofEpochMilli(i.end).atZone(zone).toOffsetDateTime().toString()
        }
        return CalendarEvent(
            uid = UID_PREFIX + i.eventId,
            summary = i.title,
            start = start,
            end = end,
            allDay = i.allDay,
            location = i.location,
            status = if (i.status == ProviderValues.STATUS_CANCELED) "CANCELLED" else null,
            myStatus = if (i.selfStatus == ProviderValues.ATTENDEE_STATUS_DECLINED) "DECLINED" else null,
            calendar = CALENDAR_PREFIX + cal.id,
            color = cal.hex,
            calendarColor = cal.hex,
            occurrenceId = i.begin.toString(),
            transparent = i.availability == ProviderValues.AVAILABILITY_FREE,
            noReminders = true,
        )
    }

    /** The occurrence's begin and end in epoch ms (all-day: UTC midnight, as the provider keeps it). */
    fun span(e: CalendarEvent): Pair<Long, Long?>? {
        val begin = e.occurrenceId?.toLongOrNull() ?: return null
        val end = e.end?.let { s ->
            if (s.length == 10) catching { LocalDate.parse(s).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli() }.getOrNull()
            else catching { OffsetDateTime.parse(s).toInstant().toEpochMilli() }.getOrNull()
        }
        return begin to end
    }

    /** "Open in Calendar": the phone's calendar app on this event, at this occurrence. */
    fun openIntent(e: CalendarEvent): Intent? {
        val id = eventId(e) ?: return null
        val i = Intent(Intent.ACTION_VIEW, ContentUris.withAppendedId(CalendarContract.Events.CONTENT_URI, id))
        span(e)?.let { (b, en) ->
            i.putExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, b)
            if (en != null) i.putExtra(CalendarContract.EXTRA_EVENT_END_TIME, en)
        }
        return i
    }
}

/**
 * Phone calendars (docs/design/device-calendars.md): the phone's own calendars shown in the app,
 * and the busy times of the ones set to "Show and send busy times" or "Show and send details" sent
 * to the server as snapshots. Nothing but start, end, all-day and an opaque id per busy occurrence
 * leaves the phone, and for "Show and send details" also its title and place ([PhoneCalendarPut]).
 */
class PhoneCalendars(
    private val context: Context,
    val source: PhoneCalendarSource,
    val prefs: PhoneCalendarPrefs,
    private val client: ApiClient?,
    /** This install's stable id (ApiClient.installId), part of every calendar key. */
    private val installId: String,
    /** The phone's model ("Pixel 9 Pro Fold"), shown on the server. */
    private val device: String = android.os.Build.MODEL.orEmpty(),
    private val signedIn: () -> Boolean = { true },
    /** Mailr's own events on this phone (Room): a phone event also there is shown and sent from Mailr's copy only. */
    private val mailr: MailrEvents? = null,
) {
    /** Set by the app: run the sync after [delayMs] (WorkManager, replacing a run already waiting). */
    var scheduleSoon: (delayMs: Long) -> Unit = {}
    /** Set by the app: the 30-minute run on (true) or off (false). */
    var schedulePeriodic: (on: Boolean) -> Unit = {}

    private val mutex = Mutex()

    fun key(cal: PhoneCalendar): String = PhoneBusy.calendarKey(installId, cal.accountType, cal.accountName, cal.id)

    /** The keys of the busy times this phone sent: the server sends them back as calendars, and they are not drawn twice here. */
    fun ownKeys(): Set<String> = prefs.sent().keys

    /** Calendars set to send busy times (with or without details), while the feature is on. */
    private fun sendModes(): Map<Long, PhoneCalMode> =
        if (!prefs.enabled.value) emptyMap() else prefs.modes.value.filterValues { it.sends }

    private fun sendIds(): Set<Long> = sendModes().keys

    /** The shown occurrences in [fromMs, toMs), as the calendar views draw them; follows the provider and the settings. */
    @OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
    fun events(fromMs: Long, toMs: Long, zone: () -> ZoneId = { ZoneId.systemDefault() }): Flow<List<CalendarEvent>> =
        combine(prefs.enabled, prefs.modes) { on, modes -> if (!on) emptySet() else modes.filterValues { it.shown }.keys }
            .distinctUntilChanged()
            .flatMapLatest { ids ->
                if (ids.isEmpty()) flowOf(emptyList())
                else {
                    val (mFrom, mTo) = PhoneMatch.readWindow(fromMs, toMs)
                    combine(providerChanges(CalendarContract.CONTENT_URI), mailr?.events(mFrom, mTo) ?: flowOf(emptyList())) { _, mine ->
                        val z = zone()
                        shown(ids, fromMs, toMs, z, PhoneMatch.index(mine, z))
                    }
                }
            }
            .flowOn(Dispatchers.IO)

    /**
     * The occurrences of the calendars [ids] in [fromMs, toMs), as the calendar views draw them: the
     * ones [PhoneMatch.keep] keeps, so an event also in Mailr's calendar ([mailr]) is drawn from there.
     */
    fun shown(ids: Set<Long>, fromMs: Long, toMs: Long, zone: ZoneId, mailr: PhoneMatch.Index = PhoneMatch.NONE): List<CalendarEvent> {
        if (ids.isEmpty() || !source.granted()) return emptyList()
        val cals = source.calendars().filter { it.id in ids }.associateBy { it.id }
        return source.instances(cals.keys, fromMs, toMs).filter { PhoneMatch.keep(it, mailr) }
            .mapNotNull { i -> cals[i.calendarId]?.let { PhoneEvents.toEvent(i, it, zone) } }
    }

    /** The provider's change notifications while collected (one at the start). */
    private fun providerChanges(uri: android.net.Uri): Flow<Unit> = callbackFlow {
        trySend(Unit)
        val obs = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean) { trySend(Unit) }
        }
        val registered = catching { context.contentResolver.registerContentObserver(uri, true, obs) }.isSuccess
        awaitClose { if (registered) catching { context.contentResolver.unregisterContentObserver(obs) } }
    }.conflate()

    private var watcher: ContentObserver? = null

    /**
     * After a settings change or at start: the 30-minute run and the provider watch are on while a
     * calendar sends busy times; a run comes at once to send a new choice or to remove one.
     */
    fun follow(changed: Boolean = false) {
        val sending = sendIds().isNotEmpty() && source.granted() && signedIn()
        schedulePeriodic(sending)
        watch(sending)
        val held = prefs.sent().isNotEmpty()
        // A choice changed, nothing sent yet, or the server still holds calendars this phone no longer sends.
        if (signedIn() && (changed || sending && !held || !sending && held)) scheduleSoon(0)
    }

    /** The ContentObserver on CalendarContract.Events: a change sends again a minute after the last one. */
    private fun watch(on: Boolean) = synchronized(this) {
        val r = context.contentResolver
        if (on && watcher == null) {
            val obs = object : ContentObserver(Handler(Looper.getMainLooper())) {
                override fun onChange(selfChange: Boolean) { scheduleSoon(DEBOUNCE_MS) }
            }
            if (catching { r.registerContentObserver(CalendarContract.Events.CONTENT_URI, true, obs) }.isSuccess) watcher = obs
        } else if (!on) {
            watcher?.let { catching { r.unregisterContentObserver(it) } }
            watcher = null
        }
    }

    /** Whether the provider watch is on (tests). */
    val watching: Boolean get() = synchronized(this) { watcher != null }

    /** Settings: the switch. On only with READ_CALENDAR; the screen asks for it first. */
    fun setEnabled(on: Boolean) {
        prefs.setEnabled(on && source.granted())
        follow(changed = true)
    }

    /**
     * Settings: one calendar's choice. Off or Show on this phone removes its busy times from the
     * server; from "Show and send details" to "Show and send busy times" sends the snapshot without
     * titles at once, and the server erases the titles and places it held.
     */
    fun setMode(calendarId: Long, mode: PhoneCalMode) {
        prefs.setMode(calendarId, mode)
        follow(changed = true)
    }

    /**
     * The answer to the READ_CALENDAR request. Allowed: the feature is on. Denied: it stays off, and
     * when Android will not ask again ([canAskAgain] false, "Don't ask again") the screen points to
     * the app's system settings.
     */
    fun permissionAnswered(granted: Boolean, canAskAgain: Boolean) {
        if (granted) {
            prefs.setDeniedForever(false)
            prefs.setEnabled(true)
        } else {
            prefs.setEnabled(false)
            prefs.setDeniedForever(!canAskAgain)
        }
        follow(changed = true)
    }

    /** Back from the system settings: access allowed there clears the "not allowed" note; taken back there turns the feature off. */
    fun recheck() {
        val ok = source.granted()
        if (ok && prefs.deniedForever.value) prefs.setDeniedForever(false)
        if (!ok && prefs.enabled.value) { prefs.setEnabled(false); follow(changed = true) }
    }

    enum class SyncResult { DONE, RETRY }

    /**
     * One run: PUT the snapshot of each calendar that sends busy times, with titles and places for
     * "Show and send details" (skipped when its hash is the one last sent, unless that was more than
     * [REFRESH_MS] ago, so the server's freshness line stays current), and DELETE every key the
     * server holds that no calendar sends any more (switched to Off or Show on this phone, the
     * feature off, the permission taken back, the calendar gone).
     */
    suspend fun sync(now: Long = AppClock.millis()): SyncResult = mutex.withLock {
        withContext(Dispatchers.IO) {
            val api = client ?: return@withContext SyncResult.DONE
            if (!signedIn()) return@withContext SyncResult.DONE
            var result = SyncResult.DONE
            val sent = prefs.sent()
            val modes = if (source.granted()) sendModes() else emptyMap()
            val cals = if (modes.isEmpty()) emptyList() else source.calendars().filter { it.id in modes }
            val wanted = HashSet<String>()
            val window = PhoneBusy.window(now)
            // Mailr's own events in the window, from Room: empty before the calendar's first sync, and then nothing matches.
            val mine = if (cals.isEmpty() || mailr == null) PhoneMatch.NONE else {
                val (mFrom, mTo) = PhoneMatch.readWindow(window.first, window.second)
                PhoneMatch.index(catching { mailr.eventsNow(mFrom, mTo) }.getOrDefault(emptyList()), ZoneId.systemDefault())
            }
            for (cal in cals) {
                val key = key(cal)
                wanted += key
                val details = modes[cal.id] == PhoneCalMode.DETAILS
                val body = PhoneBusy.body(cal, device, key, source.instances(setOf(cal.id), window.first, window.second), window, mine, details)
                val hash = PhoneBusy.hash(body)
                val before = sent[key]
                if (before != null && before.hash == hash && now - before.at < REFRESH_MS) continue
                try {
                    api.send("PUT", "$PATH/$key", PhoneCalendarStored.serializer(), MailrJson.encodeToString(PhoneCalendarPut.serializer(), body))
                    prefs.putSent(PhoneCalendarPrefs.Sent(key, cal.id, hash, now))
                } catch (e: ApiException) {
                    Log.w("mailr", "phone calendar not sent: HTTP ${e.code}")
                    if (e.code >= 500 || e.code == 408 || e.code == 429) result = SyncResult.RETRY
                } catch (e: IOException) {
                    result = SyncResult.RETRY
                }
            }
            for (key in sent.keys - wanted) {
                try {
                    api.send("DELETE", "$PATH/$key", JsonObject.serializer(), body = null)
                    prefs.removeSent(key)
                } catch (e: ApiException) {
                    if (e.code == 404 || e.code == 410) prefs.removeSent(key)
                    else if (e.code >= 500 || e.code == 408 || e.code == 429) result = SyncResult.RETRY
                    else Log.w("mailr", "phone calendar not removed: HTTP ${e.code}")
                } catch (e: IOException) {
                    result = SyncResult.RETRY
                }
            }
            result
        }
    }

    companion object {
        const val PATH = "calendar/phone-calendars"
        /** The provider watch waits this long after the last change. */
        const val DEBOUNCE_MS = 60_000L
        /** An unchanged snapshot is sent again after this long, for the server's "Updated … ago". */
        const val REFRESH_MS = 12 * 3_600_000L
    }
}
