package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.CalendarApi
import io.github.murtazanasir.mailr.data.api.prep
import io.github.murtazanasir.mailr.data.api.prepSummary
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.ConnectorSyncStatus
import io.github.murtazanasir.mailr.data.api.FindTimeRequest
import io.github.murtazanasir.mailr.data.api.FindTimeResult
import io.github.murtazanasir.mailr.data.api.Occurrence
import io.github.murtazanasir.mailr.data.api.Person
import io.github.murtazanasir.mailr.data.cal.CalConflict
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import kotlinx.coroutines.flow.mapLatest
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import java.io.IOException
import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.EventDraft
import io.github.murtazanasir.mailr.data.api.TaskDraft
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.api.toDraft
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.cal.WriteResult
import io.github.murtazanasir.mailr.ui.mail.UiMessage
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.ZoneId
import java.time.temporal.TemporalAdjusters
import java.time.temporal.WeekFields
import java.util.Locale
import io.github.murtazanasir.mailr.data.cal.Zones

enum class CalView(@androidx.annotation.StringRes private val labelRes: Int, val days: Int) {
    AGENDA(R.string.cal_view_schedule, 0), DAY(R.string.cal_view_day, 1), THREE_DAY(R.string.cal_view_3_days, 3),
    WORK_WEEK(R.string.cal_view_work_week, 5), WEEK(R.string.cal_view_week, 7), MONTH(R.string.cal_view_month, 0);

    val label: String get() = AppText.get(labelRes)

    val grid: Boolean get() = days > 0
}

/** A minute ticker so "now" lines, sections and relative times stay current. */
private val minuteTicks = flow {
    while (true) {
        emit(AppClock.millis())
        delay(60_000 - AppClock.millis() % 60_000)
    }
}

fun weekStart(d: LocalDate): LocalDate = d.with(TemporalAdjusters.previousOrSame(WeekFields.of(Locale.getDefault()).firstDayOfWeek))

/** The days a grid view shows around [anchor]. */
fun gridDays(view: CalView, anchor: LocalDate): List<LocalDate> = when (view) {
    CalView.DAY -> listOf(anchor)
    CalView.THREE_DAY -> (0L until 3L).map { anchor.plusDays(it) }
    CalView.WORK_WEEK -> anchor.with(TemporalAdjusters.previousOrSame(DayOfWeek.MONDAY)).let { m -> (0L until 5L).map { m.plusDays(it) } }
    CalView.WEEK -> weekStart(anchor).let { s -> (0L until 7L).map { s.plusDays(it) } }
    else -> emptyList()
}

/** A task with a due date drawn on the calendar ("Show tasks on calendar"). */
data class TaskChip(val task: TaskItem, val at: LocalDateTime, val dateOnly: Boolean)

/** One CalDAV account in the drawer: its calendars and its sync error, if any; [authFailed] when
 *  the server refused the password. */
data class AccountGroup(val id: Long?, val name: String, val error: String?, val collections: List<CalendarCollection>,
                        val authFailed: Boolean = false)

enum class ChangeKind { MOVE, RESIZE, DELETE }

/**
 * A drop, resize or delete waiting for its answers (after a drop or a drag): the scope for a
 * series, then whether to tell the guests. [draft] holds the new
 * times; [done] is the toast text.
 */
data class PendingChange(
    val kind: ChangeKind,
    val event: CalendarEvent,
    val draft: EventDraft?,
    val askScope: Boolean,
    val askSend: Boolean,
    val done: String,
    val scope: String? = null,
) {
    val guests: Int get() = event.attendeeList.size
    /** Which question is open: "scope", "send", or null when everything is answered. */
    val step: String? get() = when {
        askScope && scope == null -> "scope"
        askSend -> "send"
        else -> null
    }
}

@OptIn(kotlinx.coroutines.FlowPreview::class, kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class CalendarViewModel(private val g: AppGraph, val prefs: CalendarPrefs = CalendarPrefs(g.context)) : ViewModel() {
    /** The app graph, for the event editor's live editing (LiveEditors.kt). */
    internal val graph: AppGraph get() = g
    private val deviceZone: ZoneId get() = ZoneId.systemDefault()
    private val _view = MutableStateFlow(CalView.AGENDA)
    val view: StateFlow<CalView> = _view.asStateFlow()
    private val _anchor = MutableStateFlow(AppClock.today())
    val anchor: StateFlow<LocalDate> = _anchor.asStateFlow()
    private val _selected = MutableStateFlow<String?>(null)
    val selected: StateFlow<String?> = _selected.asStateFlow()
    private val _messages = MutableSharedFlow<UiMessage>(extraBufferCapacity = 4)
    val messages: SharedFlow<UiMessage> = _messages

    val now: StateFlow<Long> = minuteTicks.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), AppClock.millis())
    val connected = g.calendar.connected
    val syncing = g.calendar.syncing
    val collections = g.calendar.collections
    val defaults = g.calendar.defaults
    val error = g.calendar.error

    /** The zone the grid is drawn in: the phone's, or the home zone when the travel banner said so. */
    val displayZone: StateFlow<ZoneId> = combine(prefs.homeZone, prefs.travel, now) { home, travel, _ ->
        val dev = deviceZone
        if (home != null && home != dev.id && travel[dev.id] == "home") Zones.orDefault(home, dev) else dev
    }.stateIn(viewModelScope, SharingStarted.Eagerly, deviceZone)

    /** The dates the current view shows: [from, to). */
    val range: StateFlow<Pair<LocalDate, LocalDate>> = combine(_view, _anchor) { v, a -> rangeOf(v, a) }
        .stateIn(viewModelScope, SharingStarted.Eagerly, rangeOf(CalView.AGENDA, AppClock.today()))

    val events: StateFlow<List<CalendarEvent>?> = range.flatMapLatest { (f, t) ->
        g.calendar.events(ms(f.minusDays(1)), ms(t.plusDays(1)))
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)

    /** Rows changed on this phone and not yet confirmed by the server: drawn with a dashed border. */
    val pendingKeys: StateFlow<Set<String>> = range.flatMapLatest { (f, t) ->
        g.calendar.pendingKeys(ms(f.minusDays(1)), ms(t.plusDays(1)))
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptySet())

    /** Calendar and task writes waiting in the offline queue ("Offline · 3 changes waiting"). */
    val waiting: StateFlow<Int> = g.calendar.waiting.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), 0)

    val offline: StateFlow<Boolean> = g.calendar.error.map { it == "offline" }
        .stateIn(viewModelScope, SharingStarted.Eagerly, false)

    val tasks: StateFlow<List<TaskChip>> = combine(prefs.showTasks, g.calendar.tasks(), displayZone) { on, ts, z ->
        if (!on) emptyList() else ts.filter { !it.completed && !it.cancelled && it.due != null }.mapNotNull { t ->
            val due = t.due ?: return@mapNotNull null
            if (Occurrence.isDate(due)) TaskChip(t, LocalDate.parse(due).atStartOfDay(), true)
            else ReminderPlan.instant(due, z)?.let { TaskChip(t, LocalDateTime.ofInstant(Instant.ofEpochMilli(it), z), false) }
        }
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    /** Calendars grouped by account, in the user's order; one group when the server does not say. */
    val accounts: StateFlow<List<AccountGroup>> = combine(collections, g.calendar.accounts) { cols, acc ->
        val evCols = cols.filter { it.events }
        val byId = acc.associateBy { it.id }
        evCols.groupBy { it.connectorId }.map { (id, cs) ->
            val a = id?.let { byId[it] }
            AccountGroup(id, a?.name?.ifBlank { null } ?: if (evCols.map { it.connectorId }.distinct().size > 1) AppText.get(R.string.cal_account_n, id ?: "").trim() else AppText.get(R.string.cal_calendars),
                a?.detail?.takeIf { a.status == "error" } ?: cs.firstNotNullOfOrNull { it.error ?: it.sync?.error },
                cs.sortedBy { it.sortOrder }, authFailed = a?.errorKind == "auth")
        }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    /** When the calendars were last in step with the server ("Synced n min ago"). */
    val lastSynced: StateFlow<Long?> = g.calendar.lastSyncAt

    val conflicts: StateFlow<List<CalConflict>> = g.calendar.conflicts
        .stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    private val _prompt = MutableStateFlow<PendingChange?>(null)
    /** The open question after a drop or delete: the series question or whether to send updates. */
    val prompt: StateFlow<PendingChange?> = _prompt.asStateFlow()

    private val _taskPrompt = MutableStateFlow<Pair<TaskItem, String>?>(null)
    /** A repeating task chip dropped on a new due: the series question ("This occurrence" or "All") is open. */
    val taskPrompt: StateFlow<Pair<TaskItem, String>?> = _taskPrompt.asStateFlow()

    /** Quick create text that survives closing the sheet (the quick-add line: Esc keeps the text). */
    var quickText: String = ""

    /** The calendars hidden before "Show only this", for "Show all". */
    private var beforeShowOnly: Map<String, Boolean>? = null

    init {
        viewModelScope.launch {
            _view.value = catching { CalView.valueOf(g.settings.prefs.first().calendarView) }.getOrDefault(CalView.AGENDA)
        }
        g.syncCalendarSoon()
        // Windows outside the standing sync (far months) load when the user goes there.
        viewModelScope.launch {
            range.debounce(250).distinctUntilChanged().collect { (f, t) ->
                val today = AppClock.today()
                if (f.isBefore(today.minusDays(7)) || t.isAfter(today.plusDays(43))) g.calendar.ensureRange(ms(f), ms(t))
            }
        }
        viewModelScope.launch { g.calendar.refreshSyncStatus() }
        viewModelScope.launch { loadHomeZone() }
    }

    private fun ms(d: LocalDate) = d.atStartOfDay(displayZone.value).toInstant().toEpochMilli()

    private fun rangeOf(v: CalView, a: LocalDate): Pair<LocalDate, LocalDate> = when (v) {
        CalView.AGENDA -> a.minusDays(7) to a.plusDays(60)
        CalView.DAY -> a.minusDays(1) to a.plusDays(2)
        CalView.THREE_DAY, CalView.WORK_WEEK, CalView.WEEK -> gridDays(v, a).let { it.first() to it.last().plusDays(1) }
        CalView.MONTH -> weekStart(a.withDayOfMonth(1)).let { it to it.plusDays(42) }
    }

    /** The home zone is the profile's time zone; without one, the zone the phone first had. */
    private suspend fun loadHomeZone() {
        val tz = g.calendar.profileZone()
        when {
            tz != null -> prefs.setHomeZone(tz)
            prefs.homeZone.value == null -> prefs.setHomeZone(deviceZone.id)
        }
    }

    fun setView(v: CalView) {
        _view.value = v
        viewModelScope.launch { g.settings.update { it.copy(calendarView = v.name) } }
    }

    fun setAnchor(d: LocalDate) { _anchor.value = d }
    fun today() { _anchor.value = AppClock.today(displayZone.value) }

    fun step(delta: Int) {
        _anchor.value = when (_view.value) {
            CalView.DAY -> _anchor.value.plusDays(delta.toLong())
            CalView.THREE_DAY -> _anchor.value.plusDays(3L * delta)
            CalView.WEEK, CalView.WORK_WEEK, CalView.AGENDA -> _anchor.value.plusWeeks(delta.toLong())
            CalView.MONTH -> _anchor.value.plusMonths(delta.toLong())
        }
    }

    fun select(key: String?) { _selected.value = key }

    /** A message on the snackbar. */
    fun say(text: String) { viewModelScope.launch { _messages.emit(UiMessage(text)) } }

    /** ACT calendar.sync @android.calendar: pull to refresh. */
    fun refresh() {
        viewModelScope.launch {
            if (!g.calendar.sync()) g.calendar.error.value?.let { e ->
                _messages.emit(UiMessage(if (e == "offline") AppText.get(R.string.cal_offline_showing_full) else e))
            }
        }
    }

    suspend fun master(uid: String): CalendarEvent? = g.calendar.master(uid)
    /** ACT event.prep @android.calendar: the meeting prep pack and its summary paragraph (from the server). */
    suspend fun prep(uid: String, occurrence: String?) = g.calendar.api.prep(uid, occurrence)
    suspend fun prepSummary(uid: String, occurrence: String?) = g.calendar.api.prepSummary(uid, occurrence)
    /** A task from the prep card: the Tasks tab on it (mailr://task/<uid>). */
    fun openTaskLink(context: android.content.Context, uid: String) {
        context.startActivity(io.github.murtazanasir.mailr.ui.util.InternalLinks.mark(context,
            android.content.Intent(context, io.github.murtazanasir.mailr.ui.MainActivity::class.java).setAction(android.content.Intent.ACTION_VIEW)
                .setData(android.net.Uri.parse("mailr://task").buildUpon().appendPath(uid).build())
                .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK or android.content.Intent.FLAG_ACTIVITY_CLEAR_TOP)))
    }
    suspend fun instance(key: String): CalendarEvent? = g.calendar.eventInstance(key)
    /** The next occurrence of [uid] (else its first), for a link without an occurrence. */
    suspend fun firstInstance(uid: String): CalendarEvent? {
        val all = g.calendar.eventsOf(uid)
        val now = AppClock.millis()
        return all.firstOrNull { (ReminderPlan.eventEnd(it, displayZone.value) ?: 0) >= now } ?: all.firstOrNull()
    }

    /** Guest autocomplete: People and recent recipients. */
    suspend fun people(q: String): List<Person> = g.compose.suggest(q)

    /** ACT event.find_time @android.editor */
    suspend fun findTime(req: FindTimeRequest): FindTimeResult = g.calendar.findTime(req)

    /**
     * Busy, non-declined events of my calendars that overlap [start, end) (time conflicts),
     * without [except]. Times are in the display zone.
     */
    suspend fun overlaps(start: LocalDateTime, end: LocalDateTime, except: String? = null): List<CalendarEvent> {
        val z = displayZone.value
        val s = start.atZone(z).toInstant().toEpochMilli()
        val e = end.atZone(z).toInstant().toEpochMilli()
        val busy = collections.value.filter { !it.busy }.mapTo(HashSet()) { it.url }
        return g.calendar.eventsNow(s, e).filter { ev ->
            ev.uid != except && !ev.allDay && !ev.transparent && !ev.cancelled && ev.myStatus != "DECLINED" && ev.calendar !in busy &&
                collections.value.firstOrNull { it.url == ev.calendar }?.visible != false
        }
    }

    /** "Overlaps Team sync 3:00–4:00 PM" for a conflict line, or null. */
    suspend fun conflictLine(start: LocalDateTime, end: LocalDateTime, except: String? = null): String? {
        val o = overlaps(start, end, except).firstOrNull() ?: return null
        val z = displayZone.value
        val tf = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault())
        val s = ReminderPlan.instant(o.start, z)?.let { Instant.ofEpochMilli(it).atZone(z).format(tf) }.orEmpty()
        val en = ReminderPlan.eventEnd(o, z)?.let { Instant.ofEpochMilli(it).atZone(z).format(tf) }.orEmpty()
        return AppText.get(R.string.cal_overlaps, o.summary.ifBlank { AppText.get(R.string.cal_no_title) }, s, en)
    }

    /**
     * ACT event.create @android.editor @android.calendar, ACT event.edit @android.editor @android.calendar, ACT event.reschedule @android.editor @android.calendar (a new start from the editor). With
     * [moveTo] the series then moves to that calendar (ACT event.move_calendar @android.editor @android.calendar); [patch] false
     * skips the field write when only the calendar changed.
     */
    /** [live]: the event editor's live-editing base; a live conflict goes to its bar ([LiveSave.onRefused]), not a message. */
    fun save(orig: CalendarEvent?, d: EventDraft, scope: String, rruleChanged: Boolean, patch: Boolean = true, moveTo: String? = null,
             live: io.github.murtazanasir.mailr.data.cal.LiveSave? = null, onDone: (Boolean) -> Unit = {}) {
        viewModelScope.launch {
            try {
                var r = when {
                    orig == null -> g.calendar.createEvent(d)
                    patch -> g.calendar.updateEvent(orig, d, scope, rruleChanged, live)
                    else -> WriteResult.SENT
                }
                if (orig != null && moveTo != null) {
                    val current = g.calendar.eventInstance(orig.key) ?: orig
                    if (g.calendar.moveEvent(current, moveTo) == WriteResult.QUEUED) r = WriteResult.QUEUED
                }
                val invites = d.sendUpdates == true && !d.attendees.isNullOrEmpty()
                val text = when {
                    r == WriteResult.QUEUED && invites -> AppText.get(R.string.cal_saved_offline_invites)
                    r == WriteResult.QUEUED -> AppText.get(R.string.cal_saved_offline)
                    orig == null && invites -> AppText.get(R.string.cal_event_added_invites)
                    orig == null -> AppText.get(R.string.cal_event_added)
                    else -> AppText.get(R.string.cal_event_updated)
                }
                // ACT event.edit @android.editor: Undo writes the fields back as they were (no mail). Not for
                // "this and following" (the series split), a move to another calendar, guests, or a
                // description longer than the phone's copy holds.
                val back = orig?.takeIf { patch && moveTo == null && scope != "following" && d.attendees.isNullOrEmpty() &&
                    (it.description?.length ?: 0) < 2000 }
                _messages.emit(if (back == null) UiMessage(text) else UiMessage(text, AppText.get(R.string.cal_undo)) {
                    viewModelScope.launch {
                        try {
                            val current = g.calendar.eventInstance(back.key) ?: back
                            g.calendar.updateEvent(current, draftBack(back), scope, rruleChanged)
                        } catch (x: ApiException) { _messages.emit(UiMessage(x.userMessage())) }
                    }
                })
                onDone(true)
            } catch (e: ApiException) {
                if (live == null || io.github.murtazanasir.mailr.data.cal.LiveSave.conflictOf(e) == null) _messages.emit(UiMessage(e.userMessage()))
                onDone(false)
            }
        }
    }

    /** ACT event.delete @android.editor @android.calendar, with the scope the editor asked for. */
    fun delete(e: CalendarEvent, scope: String, sendUpdates: Boolean? = null) {
        viewModelScope.launch {
            if (_selected.value == e.key) _selected.value = null
            val token = g.calendar.deleteEvent(e, scope, sendUpdates)
            _messages.emit(UiMessage(if (scope == "all" || !e.recurring) AppText.get(R.string.cal_event_deleted) else AppText.get(R.string.cal_deleted), AppText.get(R.string.cal_undo)) {
                viewModelScope.launch { g.calendar.undoDeleteEvent(token) }
            })
        }
    }

    /**
     * ACT event.rsvp @android.editor. A note goes to the organizer with the answer; [scope] is the series question's
     * answer for a series ("this", "following" or "all"). Undo puts the earlier answer back.
     */
    fun rsvp(e: CalendarEvent, response: String, note: String = "", scope: String = "all") {
        viewModelScope.launch {
            try {
                val r = g.calendar.rsvp(e, response, note, scope)
                val word = when (response) { "accepted" -> AppText.get(R.string.cal_partstat_going); "declined" -> AppText.get(R.string.cal_partstat_not_going); else -> AppText.get(R.string.cal_partstat_maybe) }
                val before = e.myStatus?.lowercase()?.takeIf { it in setOf("accepted", "tentative", "declined") }
                val text = if (r == WriteResult.QUEUED) AppText.get(R.string.cal_rsvp_queued, word) else AppText.get(R.string.cal_rsvp_sent, word)
                _messages.emit(if (before == null || before == response) UiMessage(text)
                    else UiMessage(text, AppText.get(R.string.cal_undo)) { catching { g.calendar.rsvp(e, before, "", scope) } })
            } catch (x: ApiException) {
                _messages.emit(UiMessage(x.userMessage()))
            }
        }
    }

    /** ACT event.move_calendar: the whole series moves into [url]; undo moves it back. */
    fun moveToCalendar(e: CalendarEvent, url: String) {
        if (url == e.calendar) return
        if (readOnly(e)) { viewModelScope.launch { _messages.emit(UiMessage(AppText.get(R.string.cal_read_only_calendar))) }; return }
        val name = collections.value.firstOrNull { it.url == url }?.name ?: AppText.get(R.string.cal_export_default_name)
        val from = e.calendar
        viewModelScope.launch {
            try {
                val r = g.calendar.moveEvent(e, url)
                _messages.emit(UiMessage(if (r == WriteResult.QUEUED) AppText.get(R.string.cal_moved_to_queued, name) else AppText.get(R.string.cal_moved_to, name), from?.let { AppText.get(R.string.cal_undo) }) {
                    if (from != null) catching { g.calendar.eventsOf(e.uid).firstOrNull()?.let { g.calendar.moveEvent(it, from) } }
                })
            } catch (x: ApiException) {
                _messages.emit(UiMessage(if (x.code == 404) AppText.get(R.string.cal_deleted_elsewhere) else x.userMessage()))
            }
        }
    }

    /** The event's fields as an editor draft, for the Undo of a save (the guests are not touched). */
    private fun draftBack(e: CalendarEvent) = EventDraft(e.uid, e.summary, e.start, e.end, e.allDay, e.location.orEmpty(), e.description.orEmpty(),
        null, e.rrule, e.reminders, tz = e.tz, transparent = e.transparent, classification = e.classification.ifBlank { null },
        color = e.color?.takeIf { it != e.calendarColor } ?: "", url = e.url, sendUpdates = false, sendReminders = e.ownReminders)

    /** "Restore as new": an event deleted elsewhere comes back as a new event with the same details. */
    fun restoreAsNew(e: CalendarEvent) {
        viewModelScope.launch {
            val d = EventDraft(g.calendar.newUid(), e.summary, e.start, e.end, e.allDay, e.location.orEmpty(), e.description.orEmpty(),
                e.calendar?.takeUnless { readOnlyUrl(it) || collections.value.none { c -> c.url == it } }, e.rrule, e.reminders,
                color = e.color?.takeIf { it != e.calendarColor }, transparent = e.transparent.takeIf { it },
                classification = e.classification.ifBlank { null }, sendReminders = e.ownReminders)
            try {
                g.calendar.createEvent(d)
                _messages.emit(UiMessage(AppText.get(R.string.cal_restored_as_new)))
            } catch (x: ApiException) { _messages.emit(UiMessage(x.userMessage())) }
        }
    }

    /** Busy time in my calendars (GET /calendar/freebusy), for find a time. */
    suspend fun freebusy(from: String, to: String) = g.calendar.freebusy(from, to)

    /** The account's default reminders for a new event (GET /reminders/settings), else the phone's [device] one. */
    fun userDefaultReminders(allDay: Boolean, device: Int?): List<Int> = g.calendar.eventDefaults(allDay, device)

    /** Default reminders of the calendar [url] for a new event (null: the calendar has none). */
    fun defaultRemindersOf(url: String?, allDay: Boolean): List<Int>? =
        collections.value.firstOrNull { it.url == url }?.defaultRemindersFor(allDay)

    // ---------------- direct manipulation ----------------

    private val tf get() = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault())
    private val df get() = LocaleFormats.date("EEEdMMM", "EEE d MMM")

    private fun iso(t: LocalDateTime): String = t.atZone(displayZone.value).toOffsetDateTime().toString()

    /** I organize an event with guests: a change to it may need to tell them. */
    private fun guestsAffected(e: CalendarEvent) = e.attendeeList.isNotEmpty() && !e.isInvite

    /**
     * ACT event.reschedule (a drop, the keyboard or TalkBack moves), ACT event.resize @android.calendar (an edge):
     * the new times go through the series question and the question whether to send updates,
     * then the undo toast.
     */
    fun requestTimes(e: CalendarEvent, start: LocalDateTime, end: LocalDateTime) {
        if (readOnly(e)) { viewModelScope.launch { _messages.emit(UiMessage(AppText.get(R.string.cal_read_only_calendar))) }; return }
        val z = displayZone.value
        val oldStart = ReminderPlan.instant(e.start, z)?.let { LocalDateTime.ofInstant(Instant.ofEpochMilli(it), z) }
        val resize = oldStart == start && !e.allDay
        val d = EventDraft(e.uid, e.summary, iso(start), iso(end), false, e.location.orEmpty(), e.description.orEmpty(), e.calendar,
            e.rrule, if (e.allDay) e.reminders.map { if (it < 0) 10 else it }.distinct() else e.reminders)
        val done = if (resize) AppText.get(R.string.cal_ends_at, end.toLocalTime().format(tf)) else AppText.get(R.string.cal_moved_to, AppText.get(R.string.rem_day_time, start.format(df), start.toLocalTime().format(tf)))
        open(PendingChange(if (resize) ChangeKind.RESIZE else ChangeKind.MOVE, e, d, e.recurring, guestsAffected(e), done))
    }

    /**
     * ACT event.edit: a drop into the all-day strip (from the grid, or another day of the strip).
     * A timed event becomes all-day on [day]; an all-day event moves there and keeps its length.
     */
    fun requestAllDay(e: CalendarEvent, day: LocalDate) {
        if (readOnly(e)) { viewModelScope.launch { _messages.emit(UiMessage(AppText.get(R.string.cal_read_only_calendar))) }; return }
        val oldFirst = if (e.allDay) catching { LocalDate.parse(e.start.take(10)) }.getOrNull() else null
        if (oldFirst == day) return
        val days = if (e.allDay && oldFirst != null) e.end?.let { catching { LocalDate.parse(it.take(10)) }.getOrNull() }
            ?.let { java.time.temporal.ChronoUnit.DAYS.between(oldFirst, it).coerceAtLeast(1) } ?: 1L else 1L
        val reminders = if (e.allDay) e.reminders else e.reminders.map { if (it in 0 until 1440) -540 else it }.distinct()
        val d = EventDraft(e.uid, e.summary, day.toString(), day.plusDays(days).toString(), true, e.location.orEmpty(),
            e.description.orEmpty(), e.calendar, e.rrule, reminders)
        open(PendingChange(ChangeKind.MOVE, e, d, e.recurring, guestsAffected(e), if (e.allDay) AppText.get(R.string.cal_moved_to, day.format(df)) else AppText.get(R.string.cal_all_day_on, day.format(df))))
    }

    /** ACT event.edit: an all-day event dropped into the grid becomes 1 hour at the drop time. */
    fun requestTimed(e: CalendarEvent, start: LocalDateTime) = requestTimes(e, start, start.plusHours(1))

    /** ACT event.reschedule: a Month view drop on another day; the time of day is kept. */
    fun requestMoveDays(e: CalendarEvent, days: Long) {
        if (days == 0L) return
        if (e.allDay) {
            val first = catching { LocalDate.parse(e.start.take(10)) }.getOrNull() ?: return
            return requestAllDay(e, first.plusDays(days))
        }
        val z = displayZone.value
        val s = ReminderPlan.instant(e.start, z)?.let { LocalDateTime.ofInstant(Instant.ofEpochMilli(it), z) } ?: return
        val en = ReminderPlan.eventEnd(e, z)?.let { LocalDateTime.ofInstant(Instant.ofEpochMilli(it), z) } ?: s.plusHours(1)
        requestTimes(e, s.plusDays(days), en.plusDays(days))
    }

    /**
     * ACT task.reschedule @android.calendar: a task chip dropped on a new due ([due]: a date, or a date-time in
     * the display zone). A repeating task asks the series question first.
     */
    fun requestTaskDue(t: TaskItem, due: String) {
        if (due == t.due) return
        if (t.recurring) _taskPrompt.value = t to due else commitTaskDue(t, due, null)
    }

    /** The task's series question answered ("this" or "all"), or null when it was dismissed. */
    fun answerTaskScope(scope: String?) {
        val (t, due) = _taskPrompt.value ?: return
        _taskPrompt.value = null
        if (scope != null) commitTaskDue(t, due, scope)
    }

    private fun commitTaskDue(t: TaskItem, due: String, scope: String?) {
        viewModelScope.launch {
            try {
                val r = g.calendar.rescheduleTask(t.uid, due, scope)
                val label = io.github.murtazanasir.mailr.ui.tasks.DueChip.next(due, AppClock.millis(), displayZone.value) ?: due
                _messages.emit(UiMessage(if (r == WriteResult.QUEUED) AppText.get(R.string.cal_rescheduled_to_queued, label) else AppText.get(R.string.cal_rescheduled_to, label), AppText.get(R.string.cal_undo)) {
                    catching {
                        g.calendar.task(t.uid)?.let { cur ->
                            g.calendar.updateTask(cur, cur.toDraft().copy(due = t.due, recurrence = t.rrule?.takeIf { it != cur.rrule }))
                        }
                    }
                })
            } catch (x: ApiException) { _messages.emit(UiMessage(x.userMessage())) }
        }
    }

    /** A due for [at] in the display zone (a date for a date-only drop). */
    fun dueAt(at: LocalDateTime?, day: LocalDate): String = at?.let(::iso) ?: day.toString()

    /** ACT event.delete from the grid (the floating toolbar) or the detail: scope, send-updates, undo. */
    fun requestDelete(e: CalendarEvent) {
        if (readOnly(e)) { viewModelScope.launch { _messages.emit(UiMessage(AppText.get(R.string.cal_read_only_calendar))) }; return }
        open(PendingChange(ChangeKind.DELETE, e, null, e.recurring, guestsAffected(e), AppText.get(R.string.cal_event_deleted)))
    }

    /** The editor already asked the scope: only the question whether to send updates is left, then the delete. */
    fun requestDeleteScoped(e: CalendarEvent, scope: String) {
        open(PendingChange(ChangeKind.DELETE, e, null, askScope = false, askSend = guestsAffected(e), done = AppText.get(R.string.cal_event_deleted), scope = scope))
    }

    /** A link to the occurrence on the web (Copy link): /calendar/event/{uid}/{occurrence}. */
    fun eventLink(e: CalendarEvent): String =
        g.sessions.server.trimEnd('/') + "/calendar/event/" + java.net.URLEncoder.encode(e.uid, "UTF-8") + "/" +
            java.net.URLEncoder.encode(e.occurrence, "UTF-8")

    private fun open(c: PendingChange) {
        if (c.step == null) commit(c, null) else _prompt.value = c
    }

    /** The series question answered: "this", "following" or "all". */
    fun answerScope(scope: String) {
        val c = _prompt.value ?: return
        // The server treats "following" from the first occurrence as the whole series.
        val n = c.copy(scope = scope)
        if (n.step == null) { _prompt.value = null; commit(n, null) } else _prompt.value = n
    }

    /** The question whether to send updates answered: Send (true) or Don't send (false). */
    fun answerSend(send: Boolean) {
        val c = _prompt.value ?: return
        _prompt.value = null
        commit(c, send)
    }

    /** Back, or a dismissed sheet: nothing changes. */
    fun cancelPrompt() { _prompt.value = null }

    private fun commit(c: PendingChange, send: Boolean?) {
        val e = c.event
        val scope = c.scope ?: "all"
        viewModelScope.launch {
            when (c.kind) {
                ChangeKind.DELETE -> {
                    if (_selected.value == e.key) _selected.value = null
                    val token = g.calendar.deleteEvent(e, if (e.recurring) scope else "all", send)
                    _messages.emit(UiMessage(if (send == true && c.guests > 0) AppText.get(R.string.cal_event_deleted_cancellation) else c.done, AppText.get(R.string.cal_undo)) {
                        viewModelScope.launch { g.calendar.undoDeleteEvent(token) }
                    })
                }
                ChangeKind.MOVE, ChangeKind.RESIZE -> {
                    val d = c.draft!!.copy(sendUpdates = send)
                    try {
                        val r = g.calendar.updateEvent(e, d, scope, false)
                        val moved = e.copy(start = d.start, end = d.end, occurrenceId = e.occurrence)
                        val z = displayZone.value
                        val s = ReminderPlan.instant(d.start, z)?.let { LocalDateTime.ofInstant(Instant.ofEpochMilli(it), z) }
                        val en = d.end?.let { ReminderPlan.instant(it, z) }?.let { LocalDateTime.ofInstant(Instant.ofEpochMilli(it), z) }
                        val clash = if (s != null && en != null) conflictLine(s, en, e.uid) else null
                        val text = listOfNotNull(c.done, clash, if (r == WriteResult.QUEUED) AppText.get(R.string.cal_syncs_when_online) else null).joinToString(" · ")
                        _messages.emit(UiMessage(text, AppText.get(R.string.cal_undo)) {
                            catching { g.calendar.updateEvent(moved, d.copy(start = e.start, end = e.end), scope, false) }
                        })
                    } catch (x: ApiException) {
                        _messages.emit(UiMessage(if (x.code == 404) AppText.get(R.string.cal_deleted_elsewhere) else x.userMessage()))
                    }
                }
            }
        }
    }

    /** ACT event.duplicate @android.editor @android.calendar: a copy at [start]–[end] (the floating toolbar), without its guests' answers. */
    fun duplicate(e: CalendarEvent, start: LocalDateTime? = null, end: LocalDateTime? = null) {
        viewModelScope.launch {
            val uid = g.calendar.newUid()
            val d = EventDraft(uid, e.summary, start?.let(::iso) ?: e.start, end?.let(::iso) ?: e.end, e.allDay, e.location.orEmpty(),
                e.description.orEmpty(), e.calendar?.takeUnless { readOnlyUrl(it) }, null, e.reminders, color = e.color?.takeIf { it != e.calendarColor },
                transparent = e.transparent, classification = e.classification.ifBlank { null }, sendReminders = e.ownReminders)
            try {
                g.calendar.createEvent(d)
                _messages.emit(UiMessage(AppText.get(R.string.cal_duplicated), AppText.get(R.string.cal_undo)) {
                    g.calendar.eventsOf(uid).firstOrNull()?.let { g.calendar.deleteEvent(it, "all") }
                })
            } catch (x: ApiException) {
                _messages.emit(UiMessage(x.userMessage()))
            }
        }
    }

    /** ACT event.set_color @android.editor: a per-event colour, or null for the calendar's colour. */
    fun setEventColor(e: CalendarEvent, hex: String?) {
        viewModelScope.launch {
            val old = e.color?.takeIf { it != e.calendarColor }
            try {
                g.calendar.updateEvent(e, e.toDraft().copy(color = hex ?: ""), "all", false)
                _messages.emit(UiMessage(if (hex == null) AppText.get(R.string.cal_calendar_colour) else AppText.get(R.string.cal_colour_named, CalendarColors.nameOf(hex) ?: hex), AppText.get(R.string.cal_undo)) {
                    catching { g.calendar.updateEvent(e, e.toDraft().copy(color = old ?: ""), "all", false) }
                })
            } catch (x: ApiException) { _messages.emit(UiMessage(x.userMessage())) }
        }
    }

    /** ACT event.edit: reminders changed inline in the detail (the series' reminders for a series). */
    fun setReminders(e: CalendarEvent, minutes: List<Int>) {
        viewModelScope.launch {
            // Set on purpose: [] marks the event "no reminders" (no default fires).
            try { g.calendar.updateEvent(e, e.toDraft().copy(reminders = minutes, sendReminders = true), "all", false) }
            catch (x: ApiException) { _messages.emit(UiMessage(x.userMessage())) }
        }
    }

    fun readOnly(e: CalendarEvent): Boolean = e.calendar?.let(::readOnlyUrl) == true
    private fun readOnlyUrl(url: String) = collections.value.firstOrNull { it.url == url }?.readOnly == true

    /** ACT task.complete @android.calendar: the checkbox of a task chip on the calendar. */
    fun completeTask(t: TaskItem) {
        viewModelScope.launch {
            try {
                g.calendar.setTaskDone(t.uid, true)
                _messages.emit(UiMessage(AppText.get(R.string.cal_completed_task, t.summary.take(40)), AppText.get(R.string.cal_undo)) {
                    catching { g.calendar.setTaskDone(t.uid, false) }
                })
            } catch (x: ApiException) { _messages.emit(UiMessage(x.userMessage())) }
        }
    }

    // ---------------- calendars (the drawer) ----------------

    /** ACT calendar.toggle_visibility @android.calendar: the account setting, or this phone only ("Only on this phone"). */
    fun setVisible(c: CalendarCollection, visible: Boolean) {
        beforeShowOnly = null
        viewModelScope.launch {
            try {
                if (prefs.deviceOnly.value) g.calendar.setVisibleOnDevice(c.url, visible)
                else {
                    if (c.deviceHidden != null) g.calendar.setVisibleOnDevice(c.url, null)
                    if (c.hidden == visible) g.calendar.updateCollection(c, hidden = !visible)
                }
            } catch (x: Exception) { _messages.emit(UiMessage(x.message ?: AppText.get(R.string.cal_could_not_change_calendar))) }
        }
    }

    /** Follow the account's setting again on this phone. */
    fun clearDeviceVisibility(c: CalendarCollection) {
        viewModelScope.launch { g.calendar.setVisibleOnDevice(c.url, null) }
    }

    /** ACT calendar.show_only @android.calendar: hide every other calendar; [showAll] restores the previous set. */
    fun showOnly(c: CalendarCollection) {
        val cols = collections.value.filter { it.events }
        val before = cols.associate { it.url to it.visible }
        cols.forEach { o -> if (o.visible != (o.url == c.url)) setVisible(o, o.url == c.url) }
        beforeShowOnly = before
        viewModelScope.launch { _messages.emit(UiMessage(AppText.get(R.string.cal_showing_only, c.name), AppText.get(R.string.cal_grid_show_all)) { showAll() }) }
    }

    fun showAll() {
        val before = beforeShowOnly
        collections.value.filter { it.events }.forEach { o ->
            val want = before?.get(o.url) ?: true
            if (o.visible != want) setVisible(o, want)
        }
        beforeShowOnly = null
    }

    /** ACT calendar.create @android.calendar: a new calendar on an account (online only). */
    fun createCalendar(name: String, color: String?, connectorId: Long?) {
        viewModelScope.launch {
            try {
                val c = g.calendar.createCollection(name.trim(), color, "events", connectorId)
                _messages.emit(UiMessage(AppText.get(R.string.cal_calendar_created, c.name.ifBlank { name })))
            } catch (x: IOException) {
                _messages.emit(UiMessage(AppText.get(R.string.cal_offline_new_calendar)))
            } catch (x: Exception) { _messages.emit(UiMessage((x as? ApiException)?.userMessage() ?: x.message ?: AppText.get(R.string.cal_could_not_create_calendar))) }
        }
    }

    /** ACT calendar.rename @android.calendar */
    fun renameCalendar(c: CalendarCollection, name: String) = collectionChange(AppText.get(R.string.cal_renamed_to, name.trim()), { g.calendar.updateCollection(c, name = name.trim()) }) {
        g.calendar.updateCollection(c.copy(name = name.trim()), name = c.name)
    }

    /** ACT calendar.color @android.calendar: "" goes back to the colour on the server. */
    fun colorCalendar(c: CalendarCollection, hex: String) = collectionChange(
        if (hex.isEmpty()) AppText.get(R.string.cal_colour_from_server) else AppText.get(R.string.cal_colour_named, CalendarColors.nameOf(hex) ?: hex),
        { g.calendar.updateCollection(c, color = hex) }) {
        c.color?.let { g.calendar.updateCollection(c.copy(color = hex.ifEmpty { null }), color = it) }
    }

    private fun collectionChange(done: String, run: suspend () -> Unit, undo: suspend () -> Unit) {
        viewModelScope.launch {
            try {
                run()
                _messages.emit(UiMessage(done, AppText.get(R.string.cal_undo)) { catching { undo() } })
            } catch (x: Exception) { _messages.emit(UiMessage((x as? ApiException)?.userMessage() ?: x.message ?: AppText.get(R.string.cal_could_not_change_calendar))) }
        }
    }

    /** ACT calendar.set_default @android.calendar: new events go to [c]. */
    fun setDefault(c: CalendarCollection) {
        val before = defaults.value.calendar
        viewModelScope.launch {
            try {
                g.calendar.setDefaultCalendar(c.url)
                _messages.emit(UiMessage(AppText.get(R.string.cal_new_events_go_to, c.name), AppText.get(R.string.cal_undo)) { before?.let { catching { g.calendar.setDefaultCalendar(it) } } })
            } catch (x: IOException) {
                _messages.emit(UiMessage(AppText.get(R.string.cal_offline_try_again)))
            } catch (x: ApiException) { _messages.emit(UiMessage(x.userMessage())) }
        }
    }

    /** ACT calendar.default_reminders @android.calendar: what new events in [c] start with (timed and all-day). */
    fun setDefaultReminders(c: CalendarCollection, timed: List<Int>?, allDay: List<Int>?) {
        val old = c.defaultReminders
        viewModelScope.launch {
            try {
                g.calendar.setDefaultReminders(c, timed, allDay)
                _messages.emit(UiMessage(AppText.get(R.string.cal_default_reminders_saved), AppText.get(R.string.cal_undo)) {
                    catching {
                        g.calendar.collections.value.firstOrNull { it.url == c.url }?.let { now ->
                            if (old == null) g.calendar.setDefaultReminders(now, null, null)
                            else g.calendar.updateCollection(now, defaultReminders = old)
                        }
                    }
                })
            } catch (x: Exception) { _messages.emit(UiMessage((x as? ApiException)?.userMessage() ?: x.message ?: AppText.get(R.string.cal_could_not_change_calendar))) }
        }
    }

    /** How many events a calendar holds on this phone (the delete confirmation). */
    suspend fun eventCount(c: CalendarCollection): Int = g.calendar.eventCount(c.url)

    /** Why [c] cannot be deleted, or null. */
    fun cannotDelete(c: CalendarCollection): String? = when {
        c.shared != null -> c.ownerNote
        c.readOnly -> AppText.get(R.string.cal_cannot_delete_read_only)
        c.url == defaults.value.calendar -> AppText.get(R.string.cal_cannot_delete_default)
        collections.value.count { it.events && !it.readOnly } <= 1 -> AppText.get(R.string.cal_cannot_delete_last)
        else -> null
    }

    /** ACT calendar.delete @android.calendar: no undo; [confirm] is the calendar's name. */
    fun deleteCalendar(c: CalendarCollection, confirm: String) {
        viewModelScope.launch {
            try {
                g.calendar.deleteCollection(c, confirm)
                _messages.emit(UiMessage(AppText.get(R.string.cal_deleted_named, c.name)))
            } catch (x: IOException) {
                _messages.emit(UiMessage(AppText.get(R.string.cal_offline_delete_calendar)))
            } catch (x: Exception) { _messages.emit(UiMessage((x as? ApiException)?.userMessage() ?: x.message ?: AppText.get(R.string.cal_could_not_delete_calendar))) }
        }
    }

    // ---------------- conflicts ----------------

    fun keepTheirs(c: CalConflict) { viewModelScope.launch { g.calendar.keepTheirs(c.key) } }

    fun keepMine(c: CalConflict) {
        viewModelScope.launch {
            try { g.calendar.keepMine(c.key); _messages.emit(UiMessage(AppText.get(R.string.cal_change_saved))) }
            catch (x: ApiException) { _messages.emit(UiMessage(x.userMessage())) }
        }
    }

    /** Keep both: their version stays, mine becomes a new event. */
    fun keepBoth(c: CalConflict) {
        viewModelScope.launch {
            val mine = c.mine
            val server = c.serverEvent
            fun str(k: String) = (mine?.get(k) as? JsonPrimitive)?.contentOrNull
            val start = str("start") ?: server?.start
            if (start == null) { g.calendar.keepTheirs(c.key); return@launch }
            val d = EventDraft(g.calendar.newUid(), str("summary") ?: server?.summary.orEmpty(), start, str("end") ?: server?.end,
                (mine?.get("all_day") as? JsonPrimitive)?.contentOrNull == "true" || (server?.allDay ?: false),
                str("location") ?: server?.location.orEmpty(), str("description") ?: server?.description.orEmpty(),
                str("calendar") ?: server?.calendar, null, server?.reminders.orEmpty(), sendReminders = server?.ownReminders ?: false)
            g.calendar.keepTheirs(c.key)
            try { g.calendar.createEvent(d); _messages.emit(UiMessage(AppText.get(R.string.cal_kept_both))) }
            catch (x: ApiException) { _messages.emit(UiMessage(x.userMessage())) }
        }
    }

    // ---------------- search ----------------

    /** ACT event.search @android.calendar: upcoming first, then past (most recent first). */
    suspend fun search(q: String): Pair<List<CalendarEvent>, List<CalendarEvent>> {
        val page = g.calendar.search(q, kind = "event")
        val nowMs = AppClock.millis()
        val z = displayZone.value
        val evs = page.items.mapNotNull { it.event }
        val (up, past) = evs.partition { (ReminderPlan.eventEnd(it, z) ?: 0) >= nowMs }
        return up.sortedBy { ReminderPlan.instant(it.start, z) } to past.sortedByDescending { ReminderPlan.instant(it.start, z) }
    }

    fun newUid() = g.calendar.newUid()

    /** ACT event.email_guests @android.editor: the compose fields for [which] guests of [e]. */
    fun emailGuests(e: CalendarEvent, which: String): GuestMail =
        guestMail(e, which, eventLink(e), setOfNotNull(g.sessions.session.value?.email), displayZone.value)
}

/** The compose fields of "Email guests": who, the subject and a body with the when line and a link. */
data class GuestMail(val to: List<String>, val subject: String, val body: String)

/**
 * ACT event.email_guests: [which] is "all" (with the organizer), "yes" (those who accepted) or
 * "awaiting" (those who have not answered). My own addresses ([me]) are left out.
 */
fun guestMail(e: CalendarEvent, which: String, link: String, me: Set<String>, zone: ZoneId = ZoneId.systemDefault()): GuestMail {
    val picked = e.attendeeList.filter { a ->
        when (which) {
            "yes" -> a.partstat.equals("ACCEPTED", true)
            "awaiting" -> a.partstat.isBlank() || a.partstat.equals("NEEDS-ACTION", true)
            else -> true
        }
    }.map { it.email } + if (which == "all") listOfNotNull(e.organizer) else emptyList()
    val mine = me.mapTo(HashSet()) { it.lowercase() }
    val to = picked.filter { it.isNotBlank() && it.lowercase() !in mine }.distinctBy { it.lowercase() }
    return GuestMail(to, e.summary.ifBlank { AppText.get(R.string.cal_no_title) }, "${whenLine(e, zone)}\n$link\n")
}

/**
 * A copy of the event (duplicate, restore, keep both) carries its reminders when it has its own or
 * is marked "no reminders"; otherwise they are left out and the copy gets the default, as the
 * original does ([] would mark the copy silent).
 */
val CalendarEvent.ownReminders: Boolean get() = reminders.isNotEmpty() || noReminders

/** An event as a draft that changes nothing but what the caller overrides. */
fun CalendarEvent.toDraft(): EventDraft = EventDraft(uid, summary, start, end, allDay, location.orEmpty(), description.orEmpty(), calendar,
    rrule, reminders)
