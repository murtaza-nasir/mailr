package io.github.murtazanasir.mailr.data.cal

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import androidx.room.withTransaction
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.BulkResult
import io.github.murtazanasir.mailr.data.api.CalendarApi
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.CalendarDefaults
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.ConnectorSyncStatus
import io.github.murtazanasir.mailr.data.api.Delta
import io.github.murtazanasir.mailr.data.api.EventDraft
import io.github.murtazanasir.mailr.data.api.FindTimeRequest
import io.github.murtazanasir.mailr.data.api.FindTimeResult
import io.github.murtazanasir.mailr.data.api.FreeBusy
import io.github.murtazanasir.mailr.data.api.ImportResult
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.Occurrence
import io.github.murtazanasir.mailr.data.api.ReminderSettings
import io.github.murtazanasir.mailr.data.api.SearchHit
import io.github.murtazanasir.mailr.data.api.SearchPage
import io.github.murtazanasir.mailr.data.api.TagInfo
import io.github.murtazanasir.mailr.data.api.TaskCounts
import io.github.murtazanasir.mailr.data.api.TaskDraft
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.api.shownDetail
import io.github.murtazanasir.mailr.data.api.toDraft
import io.github.murtazanasir.mailr.data.db.CalCollectionEntity
import io.github.murtazanasir.mailr.data.db.CalConflictEntity
import io.github.murtazanasir.mailr.data.db.CalEventEntity
import io.github.murtazanasir.mailr.data.db.CalMasterEntity
import io.github.murtazanasir.mailr.data.db.CalTagEntity
import io.github.murtazanasir.mailr.data.db.CalTaskEntity
import io.github.murtazanasir.mailr.data.db.KvEntity
import io.github.murtazanasir.mailr.data.db.MailrDb
import io.github.murtazanasir.mailr.data.db.PendingOpEntity
import io.github.murtazanasir.mailr.data.reminders.ReminderConfig
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.data.repo.MailRepository
import io.github.murtazanasir.mailr.i18n.AppText
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import java.io.IOException
import java.net.URLEncoder
import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import java.util.UUID

/** A deletion waiting out its Undo window before it is queued for the server. */
data class DelayedOp(val name: String, val method: String, val path: String, val description: String)

/**
 * An editor's save under live editing (core/live.py): [baseVersion] goes as `base_version`, and
 * only the fields that differ from [baseline] (the request body of the copy at that version) are
 * sent, so a field someone else changed meanwhile is not written back. [version] is the item's
 * version after the save. A live conflict (409) reaches [onRefused] instead of the conflict list.
 */
class LiveSave(
    val baseVersion: Int?,
    val baseline: JsonObject?,
    val onRefused: (JsonObject) -> Unit = {},
) {
    @Volatile var version: Int? = null
    /** Nothing differed from the baseline: no request was sent. */
    @Volatile var unchanged: Boolean = false

    /** The request body to send for [full] (with `base_version`), or null when nothing changed. */
    fun body(full: JsonObject, keep: Set<String> = emptySet()): JsonObject? {
        // Without a version (a server before live editing, or the read failed) the whole copy goes, as before.
        val b = baseline?.takeIf { baseVersion != null }
        val changed = if (b == null) full else JsonObject(full.filter { (k, v) -> k in keep || b[k] != v })
        if (b != null && changed.keys.all { it in keep }) return null
        return JsonObject(changed + (baseVersion?.let { mapOf("base_version" to JsonPrimitive(it)) } ?: emptyMap()))
    }

    companion object {
        /** The body of a live-editing 409 (`"conflict": true`), else null. */
        fun conflictOf(e: Throwable): JsonObject? {
            val x = e as? ApiException ?: return null
            if (x.code != 409) return null
            val o = x.body?.let { catching { MailrJson.parseToJsonElement(it) as? JsonObject }.getOrNull() } ?: return null
            return o.takeIf { (it["conflict"] as? JsonPrimitive)?.contentOrNull == "true" }
        }
    }
}

/** How a write went: sent now, or queued until the device is back online. */
enum class WriteResult { SENT, QUEUED }

/** POST /tasks/bulk operations. */
enum class BulkOp(val api: String) {
    COMPLETE("complete"), REOPEN("reopen"), RESCHEDULE("reschedule"), MOVE("move"), TAG("tag"), UNTAG("untag"),
    PRIORITY("priority"), DELETE("delete"),
}

/** A task list with its open-task count, for the list manager and the drawer. */
data class ListCount(val collection: CalendarCollection, val open: Int)

/**
 * A write the server refused because the item changed elsewhere (409). [serverEvent] or
 * [serverTask] is the server's copy, which Room already shows; [mine] is the change this phone
 * tried. The conflict sheet offers [CalendarRepository.keepMine] and [CalendarRepository.keepTheirs].
 */
data class CalConflict(
    val key: String,
    val kind: String,
    val uid: String,
    val detail: String,
    val method: String,
    val mine: JsonObject?,
    val serverEvent: CalendarEvent?,
    val serverTask: TaskItem?,
    val createdAt: Long,
)

/**
 * Calendar and tasks on the device, the same way mail works: Room is what the screens read
 * (instantly, offline too); syncs replace windows of it; writes change Room first
 * (optimistic), then go to the server, or into the shared offline queue (`pending_ops`,
 * replayed by OutboxWorker with the same Idempotency-Key). Rows changed locally are `dirty`
 * and survive syncs until their change has reached the server.
 *
 * Sync uses GET /calendar/delta with a stored cursor. A server from
 * before the calendar build answers 404 there; the repository then falls back to the agenda
 * window and the open tasks, as before.
 *
 * Writes carry the ETag the phone last saw (If-Match, and `etag` in the body so a queued write
 * keeps it). A 409 keeps the server copy in Room and records a [CalConflict]; a 404 removes
 * the item here too.
 */
class CalendarRepository(
    val api: CalendarApi,
    private val db: MailrDb,
    private val mail: MailRepository,
    private val now: () -> Long = AppClock::millis,
    private val zone: () -> ZoneId = ZoneId::systemDefault,
) {
    private val dao = db.calendar()
    private val syncLock = Mutex()

    private val _collections = MutableStateFlow<List<CalendarCollection>>(emptyList())
    /** Every calendar and task list, hidden ones too (see [CalendarCollection.visible]), in the user's order. */
    val collections: StateFlow<List<CalendarCollection>> = _collections.asStateFlow()
    private val _defaults = MutableStateFlow(CalendarDefaults())
    val defaults: StateFlow<CalendarDefaults> = _defaults.asStateFlow()
    /** null until the first answer from the server (or the cache). */
    private val _connected = MutableStateFlow<Boolean?>(null)
    val connected: StateFlow<Boolean?> = _connected.asStateFlow()
    private val _syncing = MutableStateFlow(false)
    val syncing: StateFlow<Boolean> = _syncing.asStateFlow()
    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()
    private val _reminderSettings = MutableStateFlow<ReminderSettings?>(null)
    /** The account's reminder settings (GET /reminders/settings); null until known, or on an older server. */
    val reminderSettings: StateFlow<ReminderSettings?> = _reminderSettings.asStateFlow()
    private val _accounts = MutableStateFlow<List<ConnectorSyncStatus>>(emptyList())
    /** The CalDAV accounts with their sync state and errors (GET /calendar/sync-status). */
    val accounts: StateFlow<List<ConnectorSyncStatus>> = _accounts.asStateFlow()
    private val _lastSyncAt = MutableStateFlow<Long?>(null)
    /**
     * When the calendars were last in step with the CalDAV servers ("Synced n min ago"): the
     * server's `last_sync_at`, else the end of this phone's last sync without an error.
     */
    val lastSyncAt: StateFlow<Long?> = _lastSyncAt.asStateFlow()

    /** Calendar and task writes waiting in the offline queue ("Offline · 3 changes waiting"). */
    val waiting: Flow<Int> get() = db.pending().count().map { db.pending().calendarCount() }

    /** null: not asked yet; false: the server has no /calendar/delta (until the app restarts). */
    @Volatile var deltaSupported: Boolean? = null
        private set

    /** Set by the app graph: re-plan reminders after anything changed. */
    var onChanged: suspend () -> Unit = {}
    /** Set by the app graph: run [DelayedOp] after the Undo window (WorkManager), or cancel it. */
    var scheduleDelayed: (DelayedOp, Long) -> Unit = { _, _ -> }
    var cancelDelayed: (String) -> Unit = {}

    init {
        mail.onRejected = { op, e -> onQueuedRejected(op, e) }
    }

    // ---------------- reads ----------------

    fun events(fromMs: Long, toMs: Long): Flow<List<CalendarEvent>> =
        dao.events(fromMs, toMs).combine(_collections) { rows, cols ->
            val colors = cols.associate { it.url to it.color }
            val hidden = cols.filterNot { it.visible }.mapTo(HashSet()) { it.url }
            rows.filter { it.calendar !in hidden }
                .mapNotNull { r -> decodeEvent(r)?.let { e -> e.copy(color = e.color ?: e.calendarColor ?: colors[e.calendar]) } }
        }

    /** Every task that is not deleted, minus lists hidden on this phone. */
    fun tasks(): Flow<List<TaskItem>> = dao.tasks().combine(_collections) { rows, cols ->
        val hidden = cols.filterNot { it.visible }.mapTo(HashSet()) { it.url }
        rows.filter { it.list == null || it.list !in hidden }.mapNotNull(::decodeTask)
    }

    /** A smart list, a list, a tag or a parent's subtasks ([TaskFilter]), sorted. */
    fun tasksFor(filter: TaskFilter): Flow<List<TaskItem>> = tasks().map { TaskQueries.filter(it, filter, now(), zone()) }

    /** Open tasks per smart list, list and tag (the shape of GET /tasks/counts), from Room. */
    fun taskCounts(): Flow<TaskCounts> = tasks().map { TaskQueries.counts(it, now(), zone()) }

    /** Task lists (collections that take tasks) with their open counts. */
    fun listsWithCounts(): Flow<List<ListCount>> = combine(dao.tasks(), _collections) { rows, cols ->
        val counts = TaskQueries.counts(rows.mapNotNull(::decodeTask), now(), zone()).lists
        cols.filter { it.tasks }.map { ListCount(it, counts[it.url] ?: 0) }
    }

    /**
     * Tags with open counts: every tag a task carries, plus the ones mailr keeps a colour or
     * order for (GET /tasks/tags), in that order, then by name.
     */
    fun tags(): Flow<List<TagInfo>> = combine(dao.tags(), tasks()) { saved, tasks ->
        val counts = TaskQueries.counts(tasks, now(), zone()).tags
        val known = saved.associateBy { it.name }
        val names = (saved.map { it.name } + tasks.flatMap { it.categories }).distinct()
        names.map { n -> TagInfo(n, counts[n] ?: 0, known[n]?.color, known[n]?.ord ?: Int.MAX_VALUE) }
            .sortedWith(compareBy<TagInfo> { it.order }.thenBy { it.name.lowercase() })
    }

    /** Writes the server refused because the item changed elsewhere, oldest first (the conflict sheet). */
    val conflicts: Flow<List<CalConflict>> get() = dao.conflicts().map { rows -> rows.map(::toConflict) }

    /** Keys of the occurrences in [fromMs, toMs) changed on this phone and not yet confirmed (drawn dashed). */
    fun pendingKeys(fromMs: Long, toMs: Long): Flow<Set<String>> =
        dao.events(fromMs, toMs).map { rows -> rows.filter { it.dirty }.mapTo(HashSet()) { it.key } }

    /** How many events a calendar holds on this phone (the delete confirmation). */
    suspend fun eventCount(url: String): Int = dao.eventsInCollection(url).map { it.uid }.distinct().size

    suspend fun eventsNow(fromMs: Long, toMs: Long): List<CalendarEvent> = dao.eventsNow(fromMs, toMs).mapNotNull(::decodeEvent)

    /**
     * Every stored event, hidden calendars too, for matching phone events against Mailr's calendar
     * (data/phonecal/PhoneMatch). Read from Room only, never from the network.
     */
    val forPhoneMatch: io.github.murtazanasir.mailr.data.phonecal.MailrEvents = object : io.github.murtazanasir.mailr.data.phonecal.MailrEvents {
        override fun events(fromMs: Long, toMs: Long): Flow<List<CalendarEvent>> =
            dao.events(fromMs, toMs).map { rows -> rows.mapNotNull(::decodeEvent) }
        override suspend fun eventsNow(fromMs: Long, toMs: Long): List<CalendarEvent> = this@CalendarRepository.eventsNow(fromMs, toMs)
    }
    suspend fun tasksNow(): List<TaskItem> = dao.tasksNow().mapNotNull(::decodeTask)
    suspend fun task(uid: String): TaskItem? = dao.task(uid)?.let(::decodeTask)
    suspend fun eventInstance(key: String): CalendarEvent? = dao.event(key)?.let(::decodeEvent)
    suspend fun eventsOf(uid: String): List<CalendarEvent> = dao.eventsByUid(uid).mapNotNull(::decodeEvent)
    suspend fun subtasks(uid: String): List<TaskItem> = dao.children(uid).filterNot { it.deleted }.mapNotNull(::decodeTask)

    /** The series master (RRULE, reminders, guests), for editing "all events": Room first, then the server. */
    suspend fun master(uid: String): CalendarEvent? =
        dao.master(uid)?.let { r -> catching { MailrJson.decodeFromString(CalendarEvent.serializer(), r.json) }.getOrNull() }
            ?: catching { api.event(uid) }.getOrNull()

    private fun decodeEvent(r: CalEventEntity) = catching { MailrJson.decodeFromString(CalendarEvent.serializer(), r.json) }.getOrNull()
    private fun decodeTask(r: CalTaskEntity) = catching { MailrJson.decodeFromString(TaskItem.serializer(), r.json) }.getOrNull()
    private fun decodeCollection(r: CalCollectionEntity) =
        catching { MailrJson.decodeFromString(CalendarCollection.serializer(), r.json) }.getOrNull()?.copy(deviceHidden = r.deviceHidden)

    // ---------------- sync ----------------

    suspend fun loadCached() {
        reloadCollections()
        if (_collections.value.isEmpty()) db.cache().get(KV_COLLECTIONS)?.let { j ->
            catching { _collections.value = MailrJson.decodeFromString(ListSerializer(CalendarCollection.serializer()), j) }
        }
        db.cache().get(KV_DEFAULTS)?.let { j -> catching { _defaults.value = MailrJson.decodeFromString(CalendarDefaults.serializer(), j) } }
        db.cache().get(KV_CONNECTED)?.let { _connected.value = it == "1" }
        db.cache().get(KV_REMINDER_SETTINGS)?.let { j ->
            catching { _reminderSettings.value = MailrJson.decodeFromString(ReminderSettings.serializer(), j) }
        }
    }

    private suspend fun reloadCollections() {
        _collections.value = dao.collectionsNow().mapNotNull(::decodeCollection)
    }

    private fun collectionRow(c: CalendarCollection, deviceHidden: Boolean?) = CalCollectionEntity(
        c.url, c.calendarId, c.name, c.color, c.events, c.tasks, c.hidden, c.sortOrder, deviceHidden,
        MailrJson.encodeToString(CalendarCollection.serializer(), c))

    /** Collections from the server, keeping each one's device override. [replaceAll] drops the ones not listed. */
    private suspend fun putCollections(cols: List<CalendarCollection>, replaceAll: Boolean) {
        val old = dao.collectionsNow().associateBy { it.url }
        dao.putCollections(cols.map { collectionRow(it, old[it.url]?.deviceHidden) })
        if (replaceAll) dao.dropCollections(old.keys.filter { k -> cols.none { it.url == k } })
    }

    private suspend fun refreshDefaults() {
        catching { api.defaults() }.getOrNull()?.let { d ->
            _defaults.value = d
            db.cache().put(KvEntity(KV_DEFAULTS, MailrJson.encodeToString(CalendarDefaults.serializer(), d)))
        }
    }

    private suspend fun refreshMeta() {
        catching { api.collections() }.getOrNull()?.filter { it.error == null && it.url.isNotBlank() }?.let { cols ->
            putCollections(cols, replaceAll = true)
            reloadCollections()
        }
        refreshDefaults()
    }

    /** GET /tasks/tags into Room (colour and order). Quiet: older servers have no such route. */
    suspend fun refreshTags() {
        catching { api.tags() }.getOrNull()?.let { tags ->
            dao.replaceTags(tags.mapIndexed { i, t -> CalTagEntity(t.name, t.color, if (t.order != 0) t.order else i) })
        }
    }

    /** GET /reminders/settings, cached. Quiet: an older server keeps the phone's own settings. */
    suspend fun refreshReminderSettings() {
        val raw = catching { api.reminderSettings() }.getOrNull() ?: return
        storeReminderSettings(raw)
    }

    private suspend fun storeReminderSettings(raw: JsonObject) {
        // The raw text is cached: an explicit null ("no default reminder") must not turn into the default.
        val text = MailrJson.encodeToString(JsonObject.serializer(), raw)
        val s = catching { MailrJson.decodeFromString(ReminderSettings.serializer(), text) }.getOrNull() ?: return
        _reminderSettings.value = s
        db.cache().put(KvEntity(KV_REMINDER_SETTINGS, text))
        afterReminderSettings(s)
    }

    /** Called with the server's settings after each read (AppGraph moves a device-only Remind again up once). */
    var afterReminderSettings: suspend (ReminderSettings) -> Unit = {}

    /**
     * ACT reminder.repeat @android.notification: Remind again on the server, one choice for the phone and the web. [minutes]
     * 0 turns it off; otherwise 5, 10, 15 or 30; [max] (1 to 3) only when given. Online only.
     */
    suspend fun setRemindAgain(minutes: Int, max: Int? = null) {
        // Only what changed is sent: the server merges, so a limit chosen on the web stays.
        val repeat = if (minutes > 0) buildMap {
            put("enabled", JsonPrimitive(true)); put("every_min", JsonPrimitive(minutes))
            if (max != null) put("max", JsonPrimitive(max))
        } else mapOf("enabled" to JsonPrimitive(false))
        storeReminderSettings(api.putReminderSettings(JsonObject(mapOf("repeat" to JsonObject(repeat)))))
        onChanged()
    }

    /** PUT /reminders/settings with the whole object (nulls sent as null). Online only. */
    suspend fun updateReminderSettings(s: ReminderSettings) {
        val body = JsonObject(mapOf(
            "events" to JsonPrimitive(s.events), "tasks" to JsonPrimitive(s.tasks),
            "default_timed_event" to (s.defaultTimedEvent?.let(::JsonPrimitive) ?: JsonNull),
            "default_all_day_event" to (s.defaultAllDayEvent?.let(::JsonPrimitive) ?: JsonNull),
            "default_task" to JsonPrimitive(s.defaultTask), "morning_hour" to JsonPrimitive(s.morningHour),
            "web" to JsonObject(mapOf("desktop" to JsonPrimitive(s.web.desktop), "push" to JsonPrimitive(s.web.push))),
            "missed" to JsonPrimitive(s.missed), "sound" to JsonPrimitive(s.sound),
        ) + (s.repeat?.let { r -> mapOf("repeat" to JsonObject(mapOf("enabled" to JsonPrimitive(r.enabled),
            "every_min" to JsonPrimitive(r.everyMin), "max" to JsonPrimitive(r.max)))) } ?: emptyMap()))
        storeReminderSettings(api.putReminderSettings(body))
        onChanged()
    }

    /**
     * What the reminder planner uses: the account's settings when the server has them, else
     * [fallback] (the phone's own). Collections with `notify` off never remind.
     */
    fun reminderConfig(fallback: ReminderConfig): ReminderConfig {
        val silent = _collections.value.filterNot { it.notify }.mapTo(HashSet()) { it.url }
        val s = _reminderSettings.value ?: return fallback.copy(silentCollections = silent)
        return ReminderConfig(s.events, s.tasks, s.defaultTimedEvent, s.defaultAllDayEvent, s.defaultTask, s.morningHour, silent,
            showMissed = s.missed)
    }

    /**
     * The reminders a new event starts with when its calendar has no defaults: the account's
     * (GET /reminders/settings, cached), else the phone's own [device] reminder for timed events.
     */
    fun eventDefaults(allDay: Boolean, device: Int?): List<Int> =
        _reminderSettings.value?.eventDefaults(allDay) ?: if (allDay) emptyList() else listOfNotNull(device)

    /** The profile's time zone (the home zone), or null when it has none or the server cannot say. */
    suspend fun profileZone(): String? = catching {
        (api.client.get("assistant/profile", JsonObject.serializer())["timezone"] as? JsonPrimitive)?.contentOrNull
    }.getOrNull()?.let { Zones.iana(it) }

    /** Local changes whose server call may still be pending keep their rows through a sync. */
    private suspend fun keepPredicate(): (Long) -> Boolean {
        val queued = db.pending().calendarCount() > 0
        val t = now()
        return { localAt -> queued || localAt > t - LOCAL_GRACE_MS }
    }

    suspend fun syncRange(fromMs: Long, toMs: Long) {
        val a = api.agenda(iso(fromMs), iso(toMs))
        setConnected(a.connected)
        val keep = keepPredicate()
        val rows = a.events.mapNotNull { eventRow(it) }
        dao.replaceEvents(fromMs, toMs, rows) { keep(it.localAt) }
    }

    suspend fun syncTasks(includeCompleted: Boolean = false) {
        // The delta already holds completed tasks.
        if (deltaSupported == true) return
        val t = api.tasks(if (includeCompleted) "all" else "open")
        setConnected(t.connected)
        val keep = keepPredicate()
        dao.replaceTasks(t.items.mapNotNull { taskRow(it) }, includeCompleted) { keep(it.localAt) }
    }

    /**
     * GET /calendar/sync-status: the accounts (drawer groups, error banners) and the last sync
     * time. Quiet: an older server has no such route; [syncedAt] then stands for the last sync.
     */
    suspend fun refreshSyncStatus(syncedAt: Long? = null) {
        val s = catching { api.syncStatus() }.getOrNull()
        if (s != null) _accounts.value = s.connectors
        val server = s?.lastSyncAt?.let { catching { OffsetDateTime.parse(it).toInstant().toEpochMilli() }.getOrNull() }
        (server ?: syncedAt)?.let { _lastSyncAt.value = it }
    }

    private suspend fun setConnected(v: Boolean) {
        _connected.value = v
        db.cache().put(KvEntity(KV_CONNECTED, if (v) "1" else "0"))
    }

    /**
     * Delta when the server has it, else the standing window: a week back, six weeks ahead,
     * open tasks. Then re-plan reminders.
     */
    suspend fun sync(): Boolean = syncLock.withLock {
        _syncing.value = true
        try {
            refreshReminderSettings()
            if (deltaSupported != false && delta()) {
                refreshDefaults()
                refreshTags()
            } else {
                refreshMeta()
                val today = AppClock.today(zone())
                syncRange(dayStart(today.minusDays(7)), dayStart(today.plusDays(43)))
                syncTasks(false)
            }
            _error.value = null
            refreshSyncStatus(now())
            true
        } catch (e: IOException) {
            _error.value = "offline"
            false
        } catch (e: ApiException) {
            if (e.code == 409) setConnected(false) else _error.value = e.shownDetail()
            false
        } finally {
            _syncing.value = false
            onChanged()
        }
    }

    /**
     * ACT calendar.sync @android.calendar: GET /calendar/delta from the stored cursor until `more` is false.
     * A first run, a `reset`, or a window that has drifted (time moves on; the instances of
     * series are kept for [WINDOW_BACK] days back to [WINDOW_AHEAD] ahead) starts again from
     * cursor 0, and then everything the full answer did not mention is removed. Returns false
     * when the server has no delta route (404).
     */
    private suspend fun delta(): Boolean {
        val today = AppClock.today(zone())
        val storedFrom = db.cache().get(KV_DELTA_FROM)?.let { catching { LocalDate.parse(it) }.getOrNull() }
        val storedTo = db.cache().get(KV_DELTA_TO)?.let { catching { LocalDate.parse(it) }.getOrNull() }
        val drifted = storedFrom == null || storedTo == null ||
            storedFrom.isAfter(today.minusDays(WINDOW_BACK - 7)) || storedTo.isBefore(today.plusDays(WINDOW_AHEAD - 30))
        val from: LocalDate = storedFrom?.takeIf { !drifted } ?: today.minusDays(WINDOW_BACK)
        val to: LocalDate = storedTo?.takeIf { !drifted } ?: today.plusDays(WINDOW_AHEAD)
        var cursor = if (drifted) 0L else db.cache().get(KV_DELTA_CURSOR)?.toLongOrNull() ?: 0L
        var full = cursor == 0L
        val seen = Seen()
        var first = true
        for (@Suppress("UNUSED_VARIABLE") page in 1..MAX_PAGES) {
            val d = try {
                api.delta(cursor, from.toString(), to.toString())
            } catch (e: ApiException) {
                if (first && e.code == 404) {
                    deltaSupported = false
                    return false
                }
                throw e
            }
            first = false
            deltaSupported = true
            if (d.reset) {
                if (cursor == 0L) break
                cursor = 0
                full = true
                seen.clear()
                continue
            }
            val wFrom = d.window?.from?.let { catching { LocalDate.parse(it) }.getOrNull() } ?: from
            val wTo = d.window?.to?.let { catching { LocalDate.parse(it) }.getOrNull() } ?: to
            applyDelta(d, dayStart(wFrom), dayStart(wTo), if (full) seen else null)
            cursor = d.cursor
            // A full sweep stores its cursor only at the end, so an interrupted one starts over.
            if (!full) db.cache().put(KvEntity(KV_DELTA_CURSOR, cursor.toString()))
            if (!d.more) break
        }
        if (full) sweep(seen)
        db.cache().put(KvEntity(KV_DELTA_CURSOR, cursor.toString()))
        db.cache().put(KvEntity(KV_DELTA_FROM, from.toString()))
        db.cache().put(KvEntity(KV_DELTA_TO, to.toString()))
        reloadCollections()
        setConnected(_collections.value.isNotEmpty())
        return true
    }

    /** What a full answer mentioned (cursor 0); the rest is gone on the server. */
    private class Seen {
        val events = HashSet<String>()
        val tasks = HashSet<String>()
        val collections = HashSet<String>()
        fun clear() { events.clear(); tasks.clear(); collections.clear() }
    }

    /** One delta page into Room, in one transaction. Rows with a local change still on its way stay. */
    private suspend fun applyDelta(d: Delta, windowFromMs: Long, windowToMs: Long, seen: Seen?) {
        val keep = keepPredicate()
        db.withTransaction {
            putCollections(d.collections.filter { it.url.isNotBlank() }, replaceAll = false)
            seen?.collections?.addAll(d.collections.map { it.url })
            for (dc in d.deletedCollections) {
                val url = dc.url.ifBlank { dc.calendarId?.let { dao.collectionById(it)?.url }.orEmpty() }
                if (url.isEmpty()) continue
                dao.dropCollections(listOf(url))
                dao.dropEvents(dao.eventsInCollection(url).filterNot { it.dirty && keep(it.localAt) }.map { it.key })
                dao.dropTasks(dao.tasksInList(url).filterNot { it.dirty && keep(it.localAt) }.map { it.uid })
                dao.dropMastersIn(url)
            }
            for (o in d.objects) {
                if ((o["kind"] as? JsonPrimitive)?.contentOrNull == "task") {
                    val t = catching { MailrJson.decodeFromJsonElement(TaskItem.serializer(), o) }.getOrNull() ?: continue
                    seen?.tasks?.add(t.uid)
                    val old = dao.task(t.uid)
                    if (old != null && old.dirty && keep(old.localAt)) continue
                    dao.putTasks(listOf(taskRow(TaskQueries.keepAbsolute(old?.let(::decodeTask), t, zone()))))
                    continue
                }
                val e = catching { MailrJson.decodeFromJsonElement(CalendarEvent.serializer(), o) }.getOrNull() ?: continue
                seen?.events?.add(e.uid)
                dao.putMasters(listOf(CalMasterEntity(e.uid, e.id, e.calendar, e.etag, MailrJson.encodeToString(CalendarEvent.serializer(), e))))
                val rows = DeltaRows.occurrences(e, d.instances[e.uid], zone()).mapNotNull { eventRow(it) }
                val incoming = rows.mapTo(HashSet()) { it.key }
                val old = dao.eventsByUid(e.uid)
                val kept = old.filter { it.dirty && keep(it.localAt) }.mapTo(HashSet()) { it.key }
                // A series' instances are replaced inside the window; ones outside it came from an
                // agenda read of a far range and stay until that range is read again.
                val drop = old.filter { r ->
                    r.key !in kept && (!e.recurring || r.key in incoming || (r.startMs >= windowFromMs && r.startMs < windowToMs))
                }
                dao.dropEvents(drop.map { it.key })
                dao.putEvents(rows.filter { it.key !in kept })
            }
            for (x in d.deleted) {
                if (x.kind == "task") {
                    val old = dao.task(x.uid) ?: continue
                    if (!(old.dirty && keep(old.localAt))) dao.dropTasks(listOf(x.uid))
                } else {
                    dao.dropEvents(dao.eventsByUid(x.uid).filterNot { it.dirty && keep(it.localAt) }.map { it.key })
                    dao.dropMasters(listOf(x.uid))
                }
            }
        }
    }

    private suspend fun sweep(seen: Seen) {
        val keep = keepPredicate()
        db.withTransaction {
            dao.eventUids().filter { it !in seen.events }.forEach { uid ->
                dao.dropEvents(dao.eventsByUid(uid).filterNot { it.dirty && keep(it.localAt) }.map { it.key })
            }
            dao.dropMasters(dao.masterUids().filter { it !in seen.events })
            dao.dropTasks(dao.allTasks().filter { it.uid !in seen.tasks && !(it.dirty && keep(it.localAt)) }.map { it.uid })
            dao.dropCollections(dao.collectionsNow().map { it.url }.filter { it !in seen.collections })
        }
    }

    /** A window the user scrolled to (month view, far weeks). Quiet on failure. */
    suspend fun ensureRange(fromMs: Long, toMs: Long) {
        catching { syncRange(fromMs, toMs) }
    }

    /** ACT calendar.sync (live): change entities that mean "read the delta". */
    fun wantsSync(entities: Set<String>): Boolean = entities.any { it in SYNC_ENTITIES }

    // ---------------- writes ----------------

    private class Sent(val result: WriteResult, val response: JsonElement?)

    /** Send now; when offline, queue with the same key. Server rejections propagate. */
    private suspend fun send(method: String, path: String, body: String, description: String, etag: String? = null): Sent {
        val key = UUID.randomUUID().toString()
        return try {
            val headers = if (etag != null) mapOf("If-Match" to etag) else emptyMap()
            Sent(WriteResult.SENT, api.client.send(method, path, JsonElement.serializer(), body, key, headers = headers))
        } catch (e: IOException) {
            mail.enqueue(method, path, body, key, description)
            Sent(WriteResult.QUEUED, null)
        }
    }

    /** Always through the queue (notification actions: no network wait in a receiver). */
    suspend fun queue(method: String, path: String, body: String, description: String) =
        mail.enqueue(method, path, body, UUID.randomUUID().toString(), description)

    /**
     * Runs a write; on a server rejection, puts the rows back ([restore]) and then applies what
     * the rejection means (409: the server copy and a conflict; 404: the item is gone).
     */
    private suspend fun <T> guarded(method: String, path: String, body: String, restore: suspend () -> Unit,
                                    live: LiveSave? = null, block: suspend () -> T): T =
        try {
            block()
        } catch (x: ApiException) {
            restore()
            // A live conflict from an open editor goes to its conflict bar, not to the conflict list.
            val refused = live?.let { LiveSave.conflictOf(x) }
            if (refused == null) rejected(x, method, path, body)
            onChanged()
            refused?.let { live.onRefused(it) }
            throw x
        }

    private suspend fun afterWrite(result: WriteResult, resyncFrom: Long? = null) {
        if (result == WriteResult.SENT) {
            if (deltaSupported == true) catching { syncLock.withLock { delta() } }
            else if (resyncFrom != null) {
                val today = AppClock.today(zone())
                val from = minOf(resyncFrom, dayStart(today.minusDays(7)))
                catching { syncRange(from, maxOf(dayStart(today.plusDays(43)), resyncFrom + 45L * DAY)) }
            }
        }
        onChanged()
    }

    fun newUid(): String = "${UUID.randomUUID()}@mailr-android"

    /**
     * The version to send with a write to [uid]: none when an earlier write to it is still in the
     * queue (that one carries the version; this one follows it, and the server applies it to
     * the copy the first one made).
     */
    private suspend fun etagFor(kind: String, uid: String): String? {
        if (db.pending().next(500).any { target(it.path) == (kind to uid) }) return null
        return if (kind == "task") dao.task(uid)?.let { it.etag ?: decodeTask(it)?.etag }
        else dao.master(uid)?.etag ?: dao.eventsByUid(uid).firstNotNullOfOrNull { decodeEvent(it)?.etag }
    }

    private fun withEtag(body: String, etag: String?): String {
        if (etag == null) return body
        val o = catching { MailrJson.parseToJsonElement(body) as? JsonObject }.getOrNull() ?: return body
        return JsonObject(o + ("etag" to JsonPrimitive(etag))).toString()
    }

    /** A write response: the new task (or `{task, next}`), or the event with its new ETag. */
    private suspend fun applyResponse(kind: String, resp: JsonElement?, live: LiveSave? = null) {
        val o = resp as? JsonObject ?: return
        val obj = (o["task"] as? JsonObject) ?: (o["event"] as? JsonObject) ?: o
        if (obj["uid"] == null) return
        live?.version = (obj["version"] as? JsonPrimitive)?.contentOrNull?.toIntOrNull()
        if (kind == "task") {
            val t = catching { MailrJson.decodeFromJsonElement(TaskItem.serializer(), obj) }.getOrNull() ?: return
            // An older server answers without a version; the optimistic row stays until the next sync.
            if (t.etag != null) dao.putTasks(listOf(taskRow(t)))
        } else {
            val e = catching { MailrJson.decodeFromJsonElement(CalendarEvent.serializer(), obj) }.getOrNull() ?: return
            if (e.etag != null) setEventEtag(e.uid, e.etag)
        }
    }

    /** The new version reaches every stored occurrence and the master; the rows are confirmed. */
    private suspend fun setEventEtag(uid: String, etag: String) {
        dao.master(uid)?.let { m ->
            val e = catching { MailrJson.decodeFromString(CalendarEvent.serializer(), m.json) }.getOrNull()
            dao.putMasters(listOf(m.copy(etag = etag,
                json = e?.let { MailrJson.encodeToString(CalendarEvent.serializer(), it.copy(etag = etag)) } ?: m.json)))
        }
        dao.putEvents(dao.eventsByUid(uid).mapNotNull { r ->
            decodeEvent(r)?.let { r.copy(json = MailrJson.encodeToString(CalendarEvent.serializer(), it.copy(etag = etag)), dirty = false) }
        })
    }

    suspend fun createEvent(d: EventDraft): WriteResult {
        val e = CalendarEvent(uid = d.uid, summary = d.summary, start = d.start, end = d.end, allDay = d.allDay,
            location = d.location, description = d.description, calendar = d.calendar ?: _defaults.value.calendar,
            recurring = d.rrule != null, rrule = d.rrule, reminders = d.reminders,
            noReminders = d.sendReminders != false && d.reminders.isEmpty(), tz = d.tz, transparent = d.transparent ?: false,
            classification = d.classification.orEmpty(), color = d.color, categories = d.categories.orEmpty(), url = d.url,
            status = d.status)
        eventRow(e, dirty = true)?.let { dao.putEvents(listOf(it)) }
        val body = CalendarApi.eventBody(d, create = true)
        return guarded("POST", "calendar/events", body, { eventRow(e)?.let { dao.dropEvents(listOf(it.key)) } }) {
            send("POST", "calendar/events", body, AppText.get(R.string.cal_op_create_event, d.summary)).also { applyResponse("event", it.response) }.result
        }.also { afterWrite(it, eventRow(e)?.startMs) }
    }

    /**
     * Change an event. [scope] is "this", "following" or "all" for a recurring event
     * ("all" for a single one). The instance changes at once; the series follows on the
     * next sync.
     */
    suspend fun updateEvent(orig: CalendarEvent, draft: EventDraft, scope: String, rruleChanged: Boolean, live: LiveSave? = null): WriteResult {
        // Reminders go only when they were set on purpose or changed: an unchanged [] would mark the
        // event "no reminders" and take away the default it gets now.
        val d = draft.copy(sendReminders = draft.sendReminders ?: (draft.reminders != orig.reminders))
        val sent = d.sendReminders == true
        val noReminders = if (sent) d.reminders.isEmpty() else orig.noReminders
        val before = dao.eventsByUid(orig.uid)
        val t = now()
        val edited = orig.copy(summary = d.summary, start = d.start, end = d.end, allDay = d.allDay, location = d.location,
            description = d.description, calendar = d.calendar ?: orig.calendar,
            reminders = if (sent) d.reminders else orig.reminders, noReminders = noReminders,
            rrule = if (rruleChanged) d.rrule else orig.rrule, tz = d.tz ?: orig.tz, transparent = d.transparent ?: orig.transparent,
            classification = d.classification ?: orig.classification,
            // "" goes back to the calendar's colour (the server drops COLOR).
            color = if (d.color == "") null else d.color ?: orig.color,
            categories = d.categories ?: orig.categories, url = d.url ?: orig.url, status = d.status ?: orig.status,
            // A moved occurrence keeps its identity: the occurrence string is its original start.
            occurrenceId = orig.occurrence)
        val changed = before.mapNotNull { r ->
            val e = decodeEvent(r) ?: return@mapNotNull null
            when {
                r.key == orig.key -> eventRow(edited, dirty = true, localAt = t)
                scope == "all" || (scope == "following" && r.startMs >= (eventRow(orig)?.startMs ?: 0)) ->
                    eventRow(e.copy(summary = d.summary, location = d.location, description = d.description,
                        reminders = if (sent) d.reminders else e.reminders, noReminders = if (sent) noReminders else e.noReminders,
                        calendar = d.calendar ?: e.calendar), dirty = true, localAt = t)
                else -> null
            }
        }
        if (orig.key !in changed.map { it.key }) dao.dropEvents(listOf(orig.key))
        dao.putEvents(changed)
        val etag = etagFor("event", orig.uid)
        val full = MailrJson.parseToJsonElement(CalendarApi.eventBody(d, create = false, rruleChanged = rruleChanged && scope != "this")) as JsonObject
        val body = if (live == null) full else live.body(full, keep = setOf("send_updates", "identity_id"))
        if (body == null) {
            // Nothing differs from the copy the editor is based on.
            live?.unchanged = true
            dao.dropEvents(changed.map { it.key })
            dao.putEvents(before)
            onChanged()
            return WriteResult.SENT
        }
        val withScope = JsonObject(body +
            mapOf("scope" to JsonPrimitive(if (orig.recurring) scope else "all")) +
            (if (orig.recurring) mapOf("occurrence" to JsonPrimitive(orig.occurrence)) else emptyMap())).toString()
        val path = "calendar/events/${pathUid(orig.uid)}"
        val sendBody = withEtag(withScope, etag)
        return guarded("PATCH", path, sendBody, { dao.dropEvents(changed.map { it.key }); dao.putEvents(before) }, live) {
            send("PATCH", path, sendBody, AppText.get(R.string.cal_op_change_event, d.summary), etag).also { applyResponse("event", it.response, live) }.result
        }.also { afterWrite(it, minOf(eventRow(orig)?.startMs ?: t, eventRow(edited)?.startMs ?: t)) }
    }

    /**
     * Hide the event at once; the server delete waits out the Undo window ([undoDeleteEvent]).
     * Returns a token for Undo. [sendUpdates] false deletes without telling the guests.
     */
    suspend fun deleteEvent(e: CalendarEvent, scope: String, sendUpdates: Boolean? = null): String {
        val t = now()
        val start = eventRow(e)?.startMs ?: 0
        val rows = dao.eventsByUid(e.uid).filter { r ->
            when {
                !e.recurring || scope == "all" -> true
                scope == "this" -> r.key == e.key
                else -> r.startMs >= start
            }
        }
        rows.forEach { dao.markEventDeleted(it.key, true, t) }
        val params = buildList {
            if (e.recurring) { add("scope=$scope"); add("occurrence=${enc(e.occurrence)}") }
            etagFor("event", e.uid)?.let { add("etag=${enc(it)}") }
            sendUpdates?.let { add("send_updates=$it") }
        }
        val q = if (params.isEmpty()) "" else "?" + params.joinToString("&")
        val name = "caldel-" + UUID.randomUUID()
        scheduleDelayed(DelayedOp(name, "DELETE", "calendar/events/${pathUid(e.uid)}$q", AppText.get(R.string.cal_op_delete_event, e.summary)), UNDO_WINDOW_MS)
        pendingUndo[name] = rows.map { it.key }
        onChanged()
        return name
    }

    private val pendingUndo = java.util.concurrent.ConcurrentHashMap<String, List<String>>()

    suspend fun undoDeleteEvent(token: String) {
        cancelDelayed(token)
        val t = now()
        pendingUndo.remove(token)?.forEach { dao.markEventDeleted(it, false, t) }
        onChanged()
    }

    /**
     * ACT event.rsvp @android.calendar @android.sheet: Yes / Maybe / No on an invitation, with an optional note to the organizer.
     * The answer shows at once on the occurrences [scope] covers ("this", "following" or "all";
     * a single event is always "all"); offline it is queued. A series sends the scope and the
     * occurrence along.
     */
    suspend fun rsvp(e: CalendarEvent, response: String, comment: String = "", scope: String = "all"): WriteResult {
        val status = when (response) { "accepted" -> "ACCEPTED"; "declined" -> "DECLINED"; else -> "TENTATIVE" }
        val t = now()
        val z = zone()
        val rows = dao.eventsByUid(e.uid).filter { r ->
            rsvpCovers(scope, e.recurring, r.occurrence ?: decodeEvent(r)?.occurrence, e.occurrence, z)
        }
        dao.putEvents(rows.mapNotNull { r -> decodeEvent(r)?.let { eventRow(it.copy(myStatus = status), dirty = true, localAt = t) } })
        onChanged()
        val path = "calendar/events/${pathUid(e.uid)}/rsvp"
        val body = CalendarApi.json("response" to response, "comment" to comment.trim().ifEmpty { null },
            "scope" to if (e.recurring) scope else null, "occurrence" to if (e.recurring) e.occurrence else null)
        return guarded("POST", path, body, { dao.putEvents(rows) }) {
            send("POST", path, body, AppText.get(R.string.cal_op_answer, e.summary)).result
        }.also { afterWrite(it) }
    }

    /**
     * ACT event.move_calendar @android.calendar: the whole series into another calendar of the same account (UID
     * kept). Every stored occurrence moves at once; offline the move is queued.
     */
    suspend fun moveEvent(e: CalendarEvent, calendar: String): WriteResult {
        val before = dao.eventsByUid(e.uid)
        val col = _collections.value.firstOrNull { it.url == calendar }
        val t = now()
        val moved = before.mapNotNull { r ->
            decodeEvent(r)?.let { eventRow(it.copy(calendar = calendar, calendarId = col?.calendarId ?: it.calendarId,
                calendarColor = col?.color), dirty = true, localAt = t) }
        }
        dao.putEvents(moved)
        val master = dao.master(e.uid)
        master?.let { m ->
            val me = catching { MailrJson.decodeFromString(CalendarEvent.serializer(), m.json) }.getOrNull()
            dao.putMasters(listOf(m.copy(calendar = calendar,
                json = me?.let { MailrJson.encodeToString(CalendarEvent.serializer(), it.copy(calendar = calendar)) } ?: m.json)))
        }
        onChanged()
        val path = "calendar/events/${pathUid(e.uid)}/move"
        val etag = etagFor("event", e.uid)
        val body = CalendarApi.json("calendar" to calendar, "etag" to etag)
        return guarded("POST", path, body, { dao.putEvents(before); master?.let { dao.putMasters(listOf(it)) } }) {
            send("POST", path, body, AppText.get(R.string.cal_op_move, e.summary), etag).also { applyResponse("event", it.response) }.result
        }.also { afterWrite(it) }
    }

    /** ACT calendar.set_default @android.calendar, ACT list.set_default @android.tasks: PUT /calendar/defaults (online only). */
    suspend fun setDefaults(calendar: String? = null, taskList: String? = null): CalendarDefaults {
        val d = api.putDefaults(calendar, taskList)
        _defaults.value = d
        db.cache().put(KvEntity(KV_DEFAULTS, MailrJson.encodeToString(CalendarDefaults.serializer(), d)))
        return d
    }

    /** ACT calendar.set_default: new events go to [url]. */
    suspend fun setDefaultCalendar(url: String): CalendarDefaults = setDefaults(calendar = url)

    /** ACT list.set_default @android.tasks: new tasks go to [url]. */
    suspend fun setDefaultTaskList(url: String): CalendarDefaults = setDefaults(taskList = url)

    /** ACT tag.color @android.tasks: mailr keeps a tag's colour (settings.task_tags); null or "" removes it. Online only. */
    suspend fun setTagColor(name: String, color: String?) {
        api.patchTag(name, color = color ?: "")
        val tags = catching { api.tags() }.getOrNull()
        if (tags != null) dao.replaceTags(tags.mapIndexed { i, t -> CalTagEntity(t.name, t.color, if (t.order != 0) t.order else i) })
        else dao.putTags(listOf(CalTagEntity(name, color?.ifEmpty { null }, Int.MAX_VALUE)))
    }

    suspend fun createTask(d: TaskDraft): WriteResult {
        val t = TaskItem(uid = d.uid, summary = d.summary, due = d.due, dueAllDay = d.due?.length == 10, notes = d.notes,
            priority = d.priority, list = d.list ?: _defaults.value.taskList, parent = d.parent, reminders = d.reminders,
            start = d.start, startAllDay = d.start?.length == 10, rrule = d.recurrence?.ifEmpty { null },
            recurring = !d.recurrence.isNullOrEmpty(), repeatFrom = d.repeatFrom ?: "due", categories = d.categories.orEmpty(),
            sortOrder = d.sortOrder, tz = d.tz, remindersAt = d.remindersAt, conversationId = d.conversationId)
        dao.putTasks(listOf(taskRow(t, dirty = true)))
        val body = CalendarApi.taskBody(d, create = true)
        return guarded("POST", "tasks", body, { dao.dropTasks(listOf(d.uid)) }) {
            send("POST", "tasks", body, AppText.get(R.string.cal_op_add_task, d.summary)).also { applyResponse("task", it.response) }.result
        }.also { afterWrite(it) }
    }

    /** The task as [d] leaves it; fields [d] leaves null keep their value. */
    private fun edited(orig: TaskItem, d: TaskDraft): TaskItem {
        val rrule = when (d.recurrence) { null -> orig.rrule; "" -> null; else -> d.recurrence }
        val start = if (d.clearStart) null else d.start ?: orig.start
        return orig.copy(summary = d.summary, due = d.due, dueAllDay = d.due?.length == 10, notes = d.notes, priority = d.priority,
            list = d.list ?: orig.list, parent = d.parent, reminders = if (d.due == null) emptyList() else d.reminders,
            start = start, startAllDay = start?.length == 10, rrule = rrule, recurring = rrule != null,
            repeatFrom = d.repeatFrom ?: orig.repeatFrom, categories = d.categories ?: orig.categories,
            sortOrder = d.sortOrder ?: orig.sortOrder, tz = d.tz ?: orig.tz,
            remindersAt = if (d.due == null) null else d.remindersAt ?: orig.remindersAt)
    }

    suspend fun updateTask(orig: TaskItem, d: TaskDraft, live: LiveSave? = null): WriteResult {
        val full = CalendarApi.taskBody(d, create = false)
        val liveBody = live?.body(MailrJson.parseToJsonElement(full) as JsonObject)
        if (live != null && liveBody == null) {
            // Nothing differs from the copy the editor is based on.
            live.unchanged = true
            return WriteResult.SENT
        }
        val before = dao.task(orig.uid)
        dao.putTasks(listOf(taskRow(edited(orig, d), dirty = true)))
        val path = "tasks/${pathUid(orig.uid)}"
        val etag = etagFor("task", orig.uid)
        val body = withEtag(liveBody?.toString() ?: full, etag)
        return guarded("PATCH", path, body, { before?.let { dao.putTasks(listOf(it)) } }, live) {
            send("PATCH", path, body, AppText.get(R.string.cal_op_change_task, d.summary), etag).also { applyResponse("task", it.response, live) }.result
        }.also { afterWrite(it) }
    }

    /**
     * Complete or reopen. [viaQueue] for notification actions (no network wait). Completing a
     * repeating task makes the server advance it; its answer replaces the row.
     */
    suspend fun setTaskDone(uid: String, done: Boolean, viaQueue: Boolean = false): WriteResult {
        val row = dao.task(uid) ?: return WriteResult.QUEUED
        val t = decodeTask(row) ?: return WriteResult.QUEUED
        val next = t.copy(status = if (done) "completed" else "needs-action",
            completedAt = if (done) Instant.ofEpochMilli(now()).toString() else null)
        dao.putTasks(listOf(taskRow(next, dirty = true)))
        val path = "tasks/${pathUid(uid)}"
        val etag = etagFor("task", uid)
        // A completion names the due it is for, so a replay or a second device advances a repeating task once.
        val body = withEtag(CalendarApi.json("completed" to done, "occurrence" to if (done && t.recurring) t.due else null), etag)
        val desc = if (done) AppText.get(R.string.cal_op_complete_task, t.summary) else AppText.get(R.string.cal_op_reopen_task, t.summary)
        if (viaQueue) {
            queue("PATCH", path, body, desc)
            onChanged()
            return WriteResult.QUEUED
        }
        return guarded("PATCH", path, body, { dao.putTasks(listOf(row)) }) {
            send("PATCH", path, body, desc, etag).also { applyResponse("task", it.response) }.result
        }.also { if (t.recurring) afterWrite(it) else onChanged() }
    }

    /**
     * ACT task.delete @android.tasks @android.sheet @android.editor: hidden at once, sent after the Undo window. [cascade] deletes the
     * subtasks too; without it they are detached (they become top-level tasks).
     */
    suspend fun deleteTask(t: TaskItem, cascade: Boolean = false): String {
        val now = now()
        dao.task(t.uid)?.let { dao.putTasks(listOf(it.copy(deleted = true, dirty = true, localAt = now))) }
        val kids = if (cascade) dao.children(t.uid) else emptyList()
        dao.putTasks(kids.map { it.copy(deleted = true, dirty = true, localAt = now) })
        val params = buildList {
            if (cascade) add("cascade=true")
            etagFor("task", t.uid)?.let { add("etag=${enc(it)}") }
        }
        val q = if (params.isEmpty()) "" else "?" + params.joinToString("&")
        val name = "taskdel-" + UUID.randomUUID()
        scheduleDelayed(DelayedOp(name, "DELETE", "tasks/${pathUid(t.uid)}$q", AppText.get(R.string.cal_op_delete_task, t.summary)), UNDO_WINDOW_MS)
        pendingUndo[name] = listOf("task:" + t.uid) + kids.map { "task:" + it.uid }
        onChanged()
        return name
    }

    suspend fun undoDeleteTask(token: String) {
        cancelDelayed(token)
        val now = now()
        pendingUndo.remove(token)?.forEach { k ->
            dao.task(k.removePrefix("task:"))?.let { dao.putTasks(listOf(it.copy(deleted = false, localAt = now))) }
        }
        onChanged()
    }

    /**
     * ACT task.move_list @android.tasks, ACT task.reorder @android.tasks, ACT task.reparent @android.tasks: to another list, another place
     * in the manual order (after [after] and before [before], either may be null), or under
     * another parent ([parent] "" = top level, null = unchanged). The new order is the midpoint
     * between the neighbours, as the server computes it; the server's answer replaces it.
     */
    suspend fun moveTask(uid: String, list: String? = null, before: String? = null, after: String? = null, parent: String? = null): WriteResult {
        val row = dao.task(uid) ?: throw IllegalStateException(AppText.get(R.string.cal_error_task_not_found))
        val t = decodeTask(row) ?: throw IllegalStateException(AppText.get(R.string.cal_error_task_not_found))
        val order = if (before != null || after != null)
            TaskQueries.orderBetween(after?.let { task(it)?.sortOrder }, before?.let { task(it)?.sortOrder }) else t.sortOrder
        val moved = t.copy(list = list ?: t.list, sortOrder = order,
            parent = when (parent) { null -> t.parent; "" -> null; else -> parent })
        dao.putTasks(listOf(taskRow(moved, dirty = true)))
        val path = "tasks/${pathUid(uid)}/move"
        val etag = etagFor("task", uid)
        val body = CalendarApi.json("list" to list, "before" to before, "after" to after, "parent" to parent, "etag" to etag)
        return guarded("POST", path, body, { dao.putTasks(listOf(row)) }) {
            send("POST", path, body, AppText.get(R.string.cal_op_move_task, t.summary), etag).also { applyResponse("task", it.response) }.result
        }.also { afterWrite(it) }
    }

    /** ACT task.skip @android.tasks: a repeating task moves to its next due without a completed copy. */
    suspend fun skipTask(uid: String): WriteResult {
        val t = task(uid) ?: throw IllegalStateException(AppText.get(R.string.cal_error_task_not_found))
        val path = "tasks/${pathUid(uid)}/skip"
        val etag = etagFor("task", uid)
        val body = CalendarApi.json("etag" to etag)
        return guarded("POST", path, body, {}) {
            send("POST", path, body, AppText.get(R.string.cal_op_skip, t.summary), etag).also { applyResponse("task", it.response) }.result
        }.also { afterWrite(it) }
    }

    /**
     * ACT task.bulk_complete @android.tasks, ACT task.bulk_reschedule @android.tasks, ACT task.bulk_move @android.tasks, ACT task.bulk_tag @android.tasks, ACT task.bulk_priority @android.tasks,
     * ACT task.bulk_delete @android.tasks: one operation on several tasks. Room changes at once; tasks the server
     * could not change are put back as they were. [due] (reschedule; null clears), [list]
     * (move), [tag] (tag, untag), [priority]. Returns null when the operation was queued.
     */
    suspend fun bulk(uids: List<String>, op: BulkOp, due: String? = null, list: String? = null, tag: String? = null,
                     priority: Int? = null): BulkResult? {
        if (uids.isEmpty()) return BulkResult()
        val rows = uids.mapNotNull { dao.task(it) }
        val t = now()
        dao.putTasks(rows.mapNotNull { r ->
            if (op == BulkOp.DELETE) return@mapNotNull r.copy(deleted = true, dirty = true, localAt = t)
            val x = decodeTask(r) ?: return@mapNotNull null
            val y = when (op) {
                BulkOp.COMPLETE -> x.copy(status = "completed", completedAt = Instant.ofEpochMilli(t).toString())
                BulkOp.REOPEN -> x.copy(status = "needs-action", completedAt = null)
                BulkOp.RESCHEDULE -> x.copy(due = due, dueAllDay = due?.length == 10)
                BulkOp.MOVE -> x.copy(list = list ?: x.list)
                BulkOp.TAG -> x.copy(categories = (x.categories + listOfNotNull(tag)).distinct())
                BulkOp.UNTAG -> x.copy(categories = x.categories - setOfNotNull(tag))
                BulkOp.PRIORITY -> x.copy(priority = priority ?: x.priority)
                BulkOp.DELETE -> x
            }
            taskRow(y, dirty = true)
        })
        val args = buildMap<String, JsonElement> {
            if (op == BulkOp.RESCHEDULE) put("due", due?.let(::JsonPrimitive) ?: JsonNull)
            list?.let { put("list", JsonPrimitive(it)) }
            tag?.let { put("tag", JsonPrimitive(it)) }
            priority?.let { put("priority", JsonPrimitive(it)) }
        }
        val body = JsonObject(mapOf("uids" to JsonArray(uids.map(::JsonPrimitive)), "op" to JsonPrimitive(op.api),
            "args" to JsonObject(args))).toString()
        val sent = guarded("POST", "tasks/bulk", body, { dao.putTasks(rows) }) {
            send("POST", "tasks/bulk", body, "${op.api} ${uids.size} tasks")
        }
        val result = sent.response?.let { catching { MailrJson.decodeFromJsonElement(BulkResult.serializer(), it) }.getOrNull() }
        if (result != null && result.failed.isNotEmpty()) dao.putTasks(rows.filter { r -> result.failed.any { it.uid == r.uid } })
        afterWrite(sent.result)
        return if (sent.result == WriteResult.SENT) result ?: BulkResult(ok = uids) else null
    }

    suspend fun clear() {
        dao.clearEvents()
        dao.clearTasks()
        dao.clearMasters()
        dao.clearCollections()
        dao.clearTags()
        dao.clearConflicts()
        listOf(KV_DELTA_CURSOR, KV_DELTA_FROM, KV_DELTA_TO).forEach { db.cache().put(KvEntity(it, "")) }
        _collections.value = emptyList()
        deltaSupported = null
    }

    // ---------------- collections ----------------

    /** ACT calendar.create @android.calendar, ACT list.create @android.tasks: MKCALENDAR. [kind] is events, tasks or both. Online only. */
    suspend fun createCollection(name: String, color: String? = null, kind: String = "events", connectorId: Long? = null): CalendarCollection {
        val c = api.createCollection(name, color, kind, connectorId)
        putCollections(listOf(c), replaceAll = false)
        reloadCollections()
        return c
    }

    /**
     * ACT calendar.rename @android.calendar, ACT calendar.color @android.calendar, ACT list.rename @android.tasks, ACT list.color @android.tasks: name and
     * colour go to the server; hidden, busy, notify and order are mailr's own. Room changes at
     * once; offline the change is queued.
     */
    suspend fun updateCollection(c: CalendarCollection, name: String? = null, color: String? = null, hidden: Boolean? = null,
                                 busy: Boolean? = null, notify: Boolean? = null, sortOrder: Int? = null,
                                 defaultReminders: JsonElement? = null, clearDefaultReminders: Boolean = false): WriteResult {
        val id = c.calendarId ?: throw IllegalStateException(AppText.get(R.string.cal_error_cannot_change_calendars))
        if (name != null && name != c.name && c.shared != null) throw IllegalStateException(c.ownerNote)
        val old = dao.collection(c.url)
        val n = c.copy(name = name ?: c.name, color = color?.ifEmpty { null } ?: if (color == "") null else c.color,
            hidden = hidden ?: c.hidden, busy = busy ?: c.busy,
            notify = notify ?: c.notify, sortOrder = sortOrder ?: c.sortOrder,
            defaultReminders = if (clearDefaultReminders) null else defaultReminders ?: c.defaultReminders)
        dao.putCollections(listOf(collectionRow(n, old?.deviceHidden)))
        reloadCollections()
        val path = "calendar/collections/$id"
        val body = CalendarApi.json("name" to name, "color" to color, "hidden" to hidden, "busy" to busy, "notify" to notify,
            "sort_order" to sortOrder, "default_reminders" to defaultReminders, "clear_default_reminders" to clearDefaultReminders.takeIf { it })
        val sent = guarded("PATCH", path, body, { old?.let { dao.putCollections(listOf(it)) }; reloadCollections() }) {
            send("PATCH", path, body, AppText.get(R.string.cal_op_change_calendar, n.name))
        }
        (sent.response as? JsonObject)?.let { o ->
            catching { MailrJson.decodeFromJsonElement(CalendarCollection.serializer(), o) }.getOrNull()
                ?.takeIf { it.url.isNotBlank() }?.let { putCollections(listOf(it), replaceAll = false); reloadCollections() }
        }
        if (notify != null) onChanged()
        return sent.result
    }

    /**
     * ACT calendar.default_reminders @android.calendar: what new events in [c] start with, for timed and all-day
     * events (null: none of that kind). Both null clears them.
     */
    suspend fun setDefaultReminders(c: CalendarCollection, timed: List<Int>?, allDay: List<Int>?): WriteResult {
        if (timed == null && allDay == null) return updateCollection(c, clearDefaultReminders = true)
        val v = JsonObject(buildMap {
            timed?.let { put("timed", JsonArray(it.map(::JsonPrimitive))) }
            allDay?.let { put("all_day", JsonArray(it.map(::JsonPrimitive))) }
        })
        return updateCollection(c, defaultReminders = v)
    }

    /** ACT list.reorder @android.tasks: several collections in this order (drag in the list manager). */
    suspend fun reorderCollections(urls: List<String>) {
        urls.forEachIndexed { i, url ->
            _collections.value.firstOrNull { it.url == url }?.takeIf { it.sortOrder != i }?.let { updateCollection(it, sortOrder = i) }
        }
    }

    /** ACT calendar.delete @android.calendar, ACT list.delete @android.tasks: [confirm] must equal the collection's name. Online only; its items leave Room. */
    suspend fun deleteCollection(c: CalendarCollection, confirm: String) {
        val id = c.calendarId ?: throw IllegalStateException(AppText.get(R.string.cal_error_cannot_delete_calendars))
        if (c.shared != null) throw IllegalStateException(c.ownerNote)
        api.deleteCollection(id, confirm)
        db.withTransaction {
            dao.dropCollections(listOf(c.url))
            dao.dropEvents(dao.eventsInCollection(c.url).map { it.key })
            dao.dropTasks(dao.tasksInList(c.url).map { it.uid })
            dao.dropMastersIn(c.url)
        }
        reloadCollections()
        onChanged()
    }

    /**
     * ACT event.export_ics @android.calendar: the whole collection as one .ics file (GET
     * /calendar/collections/{id}/export), the copy to keep before a delete. Online only.
     */
    suspend fun exportIcs(c: CalendarCollection): ByteArray {
        val id = c.calendarId ?: throw IllegalStateException(AppText.get(R.string.cal_error_cannot_export_calendars))
        val req = okhttp3.Request.Builder().url(api.client.url("calendar/collections/$id/export")).get().build()
        val resp = api.client.raw(req)
        return kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            resp.use { r ->
                if (!r.isSuccessful) throw ApiException(r.code, ApiClient.detailOf(r.body?.string().orEmpty()) ?: AppText.get(R.string.cal_error_download_failed, c.name))
                r.body?.bytes() ?: ByteArray(0)
            }
        }
    }

    /**
     * ACT event.import_ics @android.reader: an .ics attachment (or the email's calendar part when [part] is null)
     * into [calendar] (default: the default calendar). Online only; a sync brings the events in.
     */
    suspend fun importFromMessage(messageId: Long, part: Int?, calendar: String? = null): ImportResult {
        val r = api.importFromMessage(messageId, part, calendar)
        if (r.imported.isNotEmpty()) catching { sync() }
        return r
    }

    /**
     * A task for a deep link or an app link (`/tasks/task/<uid>`): the copy on this phone, else
     * GET /tasks/{uid}, kept in Room so the editor can open it. Null when the server has no task
     * the caller can see, or the phone is offline.
     */
    suspend fun fetchTask(uid: String): TaskItem? {
        dao.task(uid)?.takeIf { !it.deleted }?.let(::decodeTask)?.let { return it }
        val t = try { api.task(uid) } catch (e: ApiException) { return null } catch (e: java.io.IOException) { return null }
        if (dao.task(uid)?.dirty != true) dao.putTasks(listOf(taskRow(t)))
        return t
    }

    /** ACT calendar.toggle_visibility @android.calendar (this phone only): [visible] null follows the account setting again. */
    suspend fun setVisibleOnDevice(url: String, visible: Boolean?) {
        dao.setDeviceHidden(url, visible?.let { !it })
        reloadCollections()
    }

    // ---------------- search, free/busy, find a time ----------------

    /**
     * ACT event.search @android.calendar, ACT task.search @android.tasks: GET /calendar/search. Offline (or on a server without
     * the route) the events and tasks in Room are searched by title, place and notes instead.
     */
    suspend fun search(q: String, kind: String = "all", from: String? = null, to: String? = null,
                       calendars: List<String> = emptyList(), limit: Int = 50, cursor: String? = null): SearchPage =
        try {
            api.search(q, kind, from, to, calendars, limit, cursor)
        } catch (e: IOException) {
            searchLocal(q, kind, limit)
        } catch (e: ApiException) {
            if (e.code == 404) searchLocal(q, kind, limit) else throw e
        }

    /** Every word in the title, place or notes; an event is its next occurrence, else its last. */
    suspend fun searchLocal(q: String, kind: String = "all", limit: Int = 50): SearchPage {
        val words = q.lowercase().split(' ').filter { it.isNotBlank() && ':' !in it }
        fun hit(vararg texts: String?) = words.isNotEmpty() && words.all { w -> texts.any { it?.lowercase()?.contains(w) == true } }
        val t = now()
        val events = if (kind == "task") emptyList() else dao.eventsNow(Long.MIN_VALUE / 2, Long.MAX_VALUE / 2).mapNotNull(::decodeEvent)
            .filter { hit(it.summary, it.location, it.description) }
            .groupBy { it.uid }.values.map { occ -> occ.firstOrNull { (ReminderPlan.instant(it.start, zone()) ?: 0) >= t } ?: occ.last() }
            .map { SearchHit("event", 0.0, it, null) }
        val tasks = if (kind == "event") emptyList() else tasksNow().filter { hit(it.summary, it.notes) }.map { SearchHit("task", 0.0, null, it) }
        return SearchPage((events + tasks).take(limit), null)
    }

    suspend fun freebusy(from: String, to: String, calendars: List<String> = emptyList()): FreeBusy = api.freebusy(from, to, calendars)

    /** ACT event.find_time @android.editor: free slots (online only). */
    suspend fun findTime(req: FindTimeRequest): FindTimeResult = api.findTime(req)

    // ---------------- conflicts and rejected writes ----------------

    private fun toConflict(r: CalConflictEntity): CalConflict {
        val server = r.serverJson?.let { catching { MailrJson.parseToJsonElement(it) }.getOrNull() }
        return CalConflict(r.key, r.kind, r.uid, r.detail, r.method,
            catching { MailrJson.parseToJsonElement(r.body) as? JsonObject }.getOrNull(),
            if (r.kind == "event") server?.let { catching { MailrJson.decodeFromJsonElement(CalendarEvent.serializer(), it) }.getOrNull() } else null,
            if (r.kind == "task") server?.let { catching { MailrJson.decodeFromJsonElement(TaskItem.serializer(), it) }.getOrNull() } else null,
            r.createdAt)
    }

    /** The kind and UID a stored write is about: "calendar/events/{uid}…" or "tasks/{uid}…". */
    private fun target(path: String): Pair<String, String>? {
        val seg = path.substringBefore('?').trim('/').split('/')
        return when {
            seg.size >= 3 && seg[0] == "calendar" && seg[1] == "events" -> "event" to seg[2]
            seg.size >= 2 && seg[0] == "tasks" && seg[1] != "bulk" -> "task" to seg[1]
            else -> null
        }
    }

    /** POST /tasks or /calendar/events: the UID is in the body. */
    private fun createTarget(path: String, body: String): Pair<String, String>? {
        val kind = when (path.substringBefore('?').trim('/')) { "tasks" -> "task"; "calendar/events" -> "event"; else -> return null }
        val uid = catching { ((MailrJson.parseToJsonElement(body) as JsonObject)["uid"] as JsonPrimitive).content }.getOrNull() ?: return null
        return kind to uid
    }

    /**
     * A queued calendar write the server refused for good (OutboxWorker → MailRepository). True
     * when the refusal is dealt with here: a 409 kept as a conflict (or a create that already
     * happened), a 404 for an item that is gone. Any other refusal stays in Pending changes.
     */
    suspend fun onQueuedRejected(op: PendingOpEntity, e: ApiException): Boolean {
        if (target(op.path) == null && createTarget(op.path, op.body) == null) return false
        val handled = e.code == 409 || (e.code == 404 && target(op.path) != null)
        if (!handled) return false
        rejected(e, op.method, op.path, op.body)
        onChanged()
        return true
    }

    /** 409: the server copy goes into Room and a conflict is kept (not for a create that already happened). 404: gone here too. */
    private suspend fun rejected(x: ApiException, method: String, path: String, body: String) {
        val (kind, uid) = target(path) ?: createTarget(path, body) ?: return
        when (x.code) {
            409 -> {
                val o = x.body?.let { catching { MailrJson.parseToJsonElement(it) as? JsonObject }.getOrNull() }
                val server = o?.get("server") as? JsonObject
                server?.let { applyServerCopy(kind, it) }
                if ((o?.get("error") as? JsonPrimitive)?.contentOrNull == "exists") return
                dao.putConflict(CalConflictEntity("$kind|$uid", kind, uid, method, path, body, server?.toString(), x.detail, now()))
            }
            404 -> if (target(path) != null) {
                if (kind == "task") dao.dropTasks(listOf(uid)) else {
                    dao.dropEventsOf(listOf(uid))
                    dao.dropMasters(listOf(uid))
                }
            }
        }
    }

    private suspend fun applyServerCopy(kind: String, server: JsonObject) {
        if (kind == "task") {
            catching { MailrJson.decodeFromJsonElement(TaskItem.serializer(), server) }.getOrNull()?.let { dao.putTasks(listOf(taskRow(it))) }
            return
        }
        val e = catching { MailrJson.decodeFromJsonElement(CalendarEvent.serializer(), server) }.getOrNull() ?: return
        dao.putMasters(listOf(CalMasterEntity(e.uid, e.id, e.calendar, e.etag, MailrJson.encodeToString(CalendarEvent.serializer(), e))))
        if (!e.recurring) {
            dao.dropEventsOf(listOf(e.uid))
            eventRow(e)?.let { dao.putEvents(listOf(it)) }
        } else e.etag?.let { setEventEtag(e.uid, it) }
    }

    /** "Keep mine": the change is sent again against the server's version. */
    suspend fun keepMine(key: String): WriteResult {
        val c = dao.conflict(key) ?: return WriteResult.SENT
        dao.dropConflict(key)
        val serverEtag = c.serverJson?.let {
            catching { ((MailrJson.parseToJsonElement(it) as JsonObject)["etag"] as JsonPrimitive).contentOrNull }.getOrNull()
        }
        val path = if (serverEtag != null && c.path.contains("etag=")) c.path.replace(Regex("etag=[^&]*"), "etag=${enc(serverEtag)}") else c.path
        // A change refused by live editing goes again as a plain write (without its base version).
        val plain = catching { (MailrJson.parseToJsonElement(c.body) as JsonObject).let { o -> JsonObject(o - "base_version").toString() } }
            .getOrDefault(c.body)
        val body = withEtag(plain, serverEtag)
        return guarded(c.method, path, body, {}) {
            send(c.method, path, body, AppText.get(R.string.cal_op_keep_my_change), serverEtag).also { applyResponse(c.kind, it.response) }.result
        }.also { afterWrite(it) }
    }

    /** "Use theirs": Room already shows the server copy; the conflict is closed. */
    suspend fun keepTheirs(key: String) = dao.dropConflict(key)

    // ---------------- reminder state ----------------
    // Notification actions must not wait for the network, so these always go through the queue.

    /** ACT task.snooze @android.notification @android.sheet, ACT event.snooze @android.notification @android.sheet */
    suspend fun queueReminderSnooze(key: String, untilMs: Long) = queue("POST", "reminders/snooze",
        CalendarApi.json("key" to key, "until" to Instant.ofEpochMilli(untilMs).toString()), AppText.get(R.string.cal_op_snooze_reminder))

    /** ACT reminder.dismiss @android.notification @android.sheet (undo = true shows it again on every device). */
    suspend fun queueReminderHandle(keys: List<String>, undo: Boolean = false) {
        if (keys.isEmpty()) return
        queue("POST", "reminders/handle", CalendarApi.json(
            "keys" to JsonArray(keys.map { JsonPrimitive(it) }),
            "undo" to undo), if (undo) AppText.get(R.string.cal_op_restore_reminder) else AppText.get(R.string.cal_op_dismiss_reminder))
    }

    /** ACT event.remind_before @android.notification @android.sheet */
    suspend fun queueReminderAdd(uid: String, occurrence: String, minutes: Int, handle: List<String>) = queue(
        "POST", "reminders/add", CalendarApi.json("kind" to "e", "uid" to uid, "occurrence" to occurrence, "minutes" to minutes,
            "handle" to JsonArray(handle.map { JsonPrimitive(it) })),
        AppText.get(R.string.cal_op_remind_before))

    /** ACT reminder.sync @android.notification: hands each page of changed states to [apply], then moves the cursor. */
    suspend fun pullReminderStates(kinds: String = "e,t", apply: suspend (List<io.github.murtazanasir.mailr.data.api.RemoteReminder>) -> Unit) {
        var cursor = db.cache().get(KV_REMINDER_CURSOR)
        repeat(10) {
            val page = api.reminderStates(cursor, kinds)
            if (page.items.isNotEmpty()) apply(page.items)
            val next = page.next ?: cursor
            if (next != null && next != cursor) db.cache().put(KvEntity(KV_REMINDER_CURSOR, next))
            cursor = next
            if (page.items.size < 500) return
        }
    }

    // ---------------- from an email ----------------

    /**
     * ACT mail.to_task @android.reader: the task as the editor left it, saved through
     * POST /calendar/from-message/confirm. The server links it to the conversation (for
     * task.open_email) and adds where it came from to the notes. Confirm takes the title, due,
     * notes and list; a priority or reminders follow as a PATCH. Online only, like the suggestion.
     */
    suspend fun taskFromMessage(messageId: Long, conversationId: Long, d: TaskDraft): TaskItem {
        val body = CalendarApi.json("message_id" to messageId, "kind" to "task", "title" to d.summary, "due" to d.due,
            "notes" to d.notes, "calendar" to d.list)
        val created = MailrJson.decodeFromJsonElement(TaskItem.serializer(), api.confirmFromMessage(body))
        val reminders = if (d.due != null) d.reminders else emptyList()
        val extras = buildList<Pair<String, Any?>> {
            if (d.priority > 0) add("priority" to d.priority)
            if (reminders.isNotEmpty()) add("reminders" to ints(reminders))
        }
        val t = created.copy(conversationId = created.conversationId ?: conversationId,
            priority = if (d.priority > 0) d.priority else created.priority, reminders = reminders.ifEmpty { created.reminders })
        dao.putTasks(listOf(taskRow(t, dirty = extras.isNotEmpty())))
        if (extras.isNotEmpty()) send("PATCH", "tasks/${pathUid(t.uid)}", CalendarApi.json(*extras.toTypedArray()),
            AppText.get(R.string.cal_op_change_task, d.summary)).also { applyResponse("task", it.response) }
        afterWrite(WriteResult.SENT)
        return t
    }

    /**
     * ACT mail.to_event @android.reader: as [taskFromMessage], for an event. Confirm takes the title, times,
     * place, notes and calendar; reminders and a repeat rule follow as a PATCH.
     */
    suspend fun eventFromMessage(messageId: Long, conversationId: Long, d: EventDraft): CalendarEvent {
        val body = CalendarApi.json("message_id" to messageId, "kind" to "event", "title" to d.summary, "start" to d.start,
            "end" to d.end, "location" to d.location, "notes" to d.description, "calendar" to d.calendar)
        val created = MailrJson.decodeFromJsonElement(CalendarEvent.serializer(), api.confirmFromMessage(body))
        // Untouched reminders stay with the server's defaults (written by the confirm); chosen ones
        // follow, where [] is "no reminders".
        val explicit = d.sendReminders ?: d.reminders.isNotEmpty()
        val extras = buildList<Pair<String, Any?>> {
            if (explicit) add("reminders" to ints(d.reminders))
            if (d.rrule != null) add("recurrence" to d.rrule)
        }
        val e = created.copy(conversationId = created.conversationId ?: conversationId,
            reminders = if (explicit) d.reminders else created.reminders,
            noReminders = if (explicit) d.reminders.isEmpty() else created.noReminders, rrule = d.rrule ?: created.rrule,
            recurring = created.recurring || d.rrule != null)
        eventRow(e, dirty = extras.isNotEmpty())?.let { dao.putEvents(listOf(it)) }
        if (extras.isNotEmpty()) send("PATCH", "calendar/events/${pathUid(e.uid)}",
            CalendarApi.json(*(extras + ("scope" to "all")).toTypedArray()), AppText.get(R.string.cal_op_change_event, d.summary))
            .also { applyResponse("event", it.response) }
        afterWrite(WriteResult.SENT, eventRow(e)?.startMs)
        return e
    }

    private fun ints(xs: List<Int>) = JsonArray(xs.map { JsonPrimitive(it) })

    /**
     * ACT task.reschedule @android.tasks @android.sheet: move the due (null = no due), keeping everything else. For a
     * repeating task [scope] is the answer to the series question: "this" (this occurrence) or "all" (the
     * series); see [TaskQueries.ruleAfterMove].
     */
    suspend fun rescheduleTask(uid: String, due: String?, scope: String? = null): WriteResult {
        val t = task(uid) ?: throw IllegalStateException(AppText.get(R.string.cal_error_task_not_found))
        val rule = if (t.recurring && scope != null) TaskQueries.ruleAfterMove(t.rrule, t.due, due, scope, zone()) else null
        return updateTask(t, t.toDraft().copy(due = due, recurrence = rule))
    }

    /** ACT event.reschedule @android.calendar @android.sheet: move one occurrence (or the series, by [scope]), keeping its duration. */
    suspend fun rescheduleEvent(e: CalendarEvent, start: String, end: String?, scope: String = "this"): WriteResult =
        updateEvent(e, EventDraft(e.uid, e.summary, start, end, e.allDay, e.location.orEmpty(), e.description.orEmpty(),
            e.calendar, e.rrule, e.reminders), if (e.recurring) scope else "all", rruleChanged = false)

    // ---------------- rows ----------------

    private fun eventRow(e: CalendarEvent, dirty: Boolean = false, localAt: Long = if (dirty) now() else 0): CalEventEntity? {
        val z = zone()
        val s = ReminderPlan.instant(e.start, z) ?: return null
        val end = ReminderPlan.eventEnd(e, z) ?: s
        // Only the event's own colour is stored; the collection's comes from the collection list when read.
        val colColor = _collections.value.firstOrNull { it.url == e.calendar }?.color
        return CalEventEntity(e.key, e.uid, s, end, e.allDay, e.calendar,
            MailrJson.encodeToString(CalendarEvent.serializer(), e.copy(color = e.color?.takeIf { it != e.calendarColor && it != colColor })),
            dirty, false, localAt, occurrence = e.occurrence, objectId = e.id, calendarId = e.calendarId)
    }

    private fun taskRow(t: TaskItem, dirty: Boolean = false): CalTaskEntity = CalTaskEntity(
        t.uid, t.list, t.parent, t.completed, t.due?.let { ReminderPlan.instant(it, zone()) },
        MailrJson.encodeToString(TaskItem.serializer(), t), dirty, false, if (dirty) now() else 0,
        objectId = t.id, listId = t.listId, startMs = TaskQueries.startMs(t, zone()), sortOrder = t.sortOrder, etag = t.etag)

    private fun iso(ms: Long): String = Instant.ofEpochMilli(ms).atZone(zone()).toOffsetDateTime().toString()
    private fun dayStart(d: LocalDate): Long = d.atStartOfDay(zone()).toInstant().toEpochMilli()
    /** For query values in a stored path (ApiClient decodes them again). */
    private fun enc(s: String): String = URLEncoder.encode(s, "UTF-8")
    /** ApiClient encodes path segments itself. (UIDs with "/" or "?" are not addressable; none seen in practice.) */
    private fun pathUid(uid: String): String = uid

    companion object {
        /**
         * The stored occurrences an answer of [scope] changes, as the server applies it:
         * `this` only [occurrence]; `following` the ones whose
         * original start (RECURRENCE-ID, so a moved override counts where it was) is [occurrence]
         * or later, while the earlier ones keep their answer; `all`, and every single event, all.
         */
        fun rsvpCovers(scope: String, recurring: Boolean, rowOccurrence: String?, occurrence: String, zone: ZoneId): Boolean {
            if (!recurring || scope == "all") return true
            if (rowOccurrence == occurrence) return true
            if (scope == "this") return false
            val row = Occurrence.epochMs(rowOccurrence, zone) ?: return false
            val from = Occurrence.epochMs(occurrence, zone) ?: return false
            return row >= from
        }

        const val DAY = 24 * 60 * 60_000L
        const val UNDO_WINDOW_MS = 7_000L
        const val LOCAL_GRACE_MS = 2 * 60_000L
        /** The delta window for the instances of series: this many days back and ahead. */
        const val WINDOW_BACK = 30L
        const val WINDOW_AHEAD = 180L
        private const val MAX_PAGES = 50
        /** Change entities (SSE `changes`) that call for a delta. */
        val SYNC_ENTITIES = setOf("event", "task", "calendar")
        private const val KV_COLLECTIONS = "cal_collections"
        private const val KV_DEFAULTS = "cal_defaults"
        private const val KV_CONNECTED = "cal_connected"
        private const val KV_REMINDER_CURSOR = "reminder_state_cursor"
        private const val KV_REMINDER_SETTINGS = "reminder_settings"
        const val KV_DELTA_CURSOR = "cal_delta_cursor"
        private const val KV_DELTA_FROM = "cal_delta_from"
        private const val KV_DELTA_TO = "cal_delta_to"
    }
}
