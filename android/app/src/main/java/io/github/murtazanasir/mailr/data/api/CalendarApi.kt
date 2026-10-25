package io.github.murtazanasir.mailr.data.api

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.cal.IanaZoneSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray

// Mirrors backend/mailr/calendar (routes.py, client.py). Unknown fields are ignored.

@Serializable
data class Attendee(
    @Serializable(with = SafeAddressSerializer::class) val email: String,
    @Serializable(with = SafeTextSerializer::class) val name: String = "",
    val partstat: String = "NEEDS-ACTION",
    val role: String = "",
    val rsvp: Boolean = false,
) {
    val display: String get() = name.ifBlank { email }
}

/**
 * One event occurrence (the server expands recurring series), or a series master from
 * GET /calendar/events/{uid}. Fields follow the server's event fields; the newer ones are absent
 * from servers before the calendar build and keep their defaults.
 */
@Serializable
data class CalendarEvent(
    val uid: String,
    val summary: String = "",
    val start: String,
    val end: String? = null,
    @SerialName("all_day") val allDay: Boolean = false,
    val location: String? = null,
    val description: String? = null,
    val status: String? = null,
    @SerialName("recurrence_id") val recurrenceId: String? = null,
    val recurring: Boolean = false,
    val rrule: String? = null,
    val organizer: String? = null,
    @SerialName("organizer_name") val organizerName: String? = null,
    @SerialName("attendee_list") val attendeeList: List<Attendee> = emptyList(),
    val reminders: List<Int> = emptyList(),
    /**
     * Marked "no reminders" on the server (X-MAILR-NO-REMINDER): the user chose none, so the
     * planner gives it no default reminder either. False for an event that simply has no VALARM.
     */
    @SerialName("no_reminders") val noReminders: Boolean = false,
    val calendar: String? = null,
    val color: String? = null,
    @SerialName("my_status") val myStatus: String? = null,
    @SerialName("is_organizer") val isOrganizer: Boolean = false,
    /** The email the event was made from (agenda only), for ACT event.open_email @android.sheet @android.editor. */
    @SerialName("conversation_id") val conversationId: Long? = null,
    /** Mirror row id of the CalDAV resource (shared by every occurrence of a series). */
    val id: Long? = null,
    /** The canonical occurrence string as the server sent it; read [occurrence]. */
    @SerialName("occurrence") val occurrenceId: String? = null,
    val etag: String? = null,
    @Serializable(with = IanaZoneSerializer::class) val tz: String? = null,
    @SerialName("end_tz") @Serializable(with = IanaZoneSerializer::class) val endTz: String? = null,
    val transparent: Boolean = false,
    @SerialName("class") val classification: String = "",
    @SerialName("calendar_color") val calendarColor: String? = null,
    val categories: List<String> = emptyList(),
    val url: String? = null,
    val sequence: Int = 0,
    @SerialName("is_override") val isOverride: Boolean = false,
    val attendees: Int = 0,
    @SerialName("calendar_id") val calendarId: Long? = null,
    @SerialName("connector_id") val connectorId: Long? = null,
    val href: String? = null,
    @SerialName("local_only") val localOnly: Boolean = false,
    /** The live version (core/live.py) from a write or GET /calendar/events/{uid}; null elsewhere. */
    val version: Int? = null,
) {
    /**
     * The canonical occurrence string: the server's `occurrence`, or (servers before the
     * calendar build) the recurrence id or start in canonical form, so keys stay the same when
     * the server is upgraded.
     */
    val occurrence: String get() = occurrenceId ?: Occurrence.canonical(recurrenceId ?: start)
    /** The instance's identity: a recurring event's instances share the UID. */
    val key: String get() = "$uid|$occurrence"
    val cancelled: Boolean get() = status.equals("CANCELLED", ignoreCase = true)
    val isInvite: Boolean get() = myStatus != null && !isOrganizer
    val isPrivate: Boolean get() = classification.equals("PRIVATE", true) || classification.equals("CONFIDENTIAL", true)
}

@Serializable
data class Agenda(val connected: Boolean = false, val events: List<CalendarEvent> = emptyList())

/** A task (VTODO). */
@Serializable
data class TaskItem(
    val uid: String,
    val summary: String = "",
    val due: String? = null,
    @SerialName("due_all_day") val dueAllDay: Boolean = false,
    val status: String = "needs-action",
    @SerialName("completed_at") val completedAt: String? = null,
    val notes: String? = null,
    val priority: Int = 0,
    val list: String? = null,
    val parent: String? = null,
    val reminders: List<Int> = emptyList(),
    @SerialName("conversation_id") val conversationId: Long? = null,
    val id: Long? = null,
    /** DTSTART: before it the task is deferred. */
    val start: String? = null,
    @SerialName("start_all_day") val startAllDay: Boolean = false,
    @Serializable(with = IanaZoneSerializer::class) val tz: String? = null,
    val percent: Int? = null,
    val recurring: Boolean = false,
    val rrule: String? = null,
    /** "due" (default) or "completion". */
    @SerialName("repeat_from") val repeatFrom: String = "due",
    /** The task's tags (CATEGORIES). */
    val categories: List<String> = emptyList(),
    /** Manual order (X-APPLE-SORT-ORDER), lower first. */
    @SerialName("sort_order") val sortOrder: Long? = null,
    @SerialName("subtask_count") val subtaskCount: Int = 0,
    @SerialName("open_subtask_count") val openSubtaskCount: Int = 0,
    @SerialName("list_id") val listId: Long? = null,
    @SerialName("connector_id") val connectorId: Long? = null,
    val href: String? = null,
    val etag: String? = null,
    val created: String? = null,
    @SerialName("last_modified") val lastModified: String? = null,
    @SerialName("local_only") val localOnly: Boolean = false,
    /**
     * Absolute reminders (UTC instants) from the task routes; null when the copy came from the
     * delta, which counts them in [reminders] as minutes before the due.
     */
    @SerialName("reminders_at") val remindersAt: List<String>? = null,
    /** The live version (core/live.py) from the task routes; null from the delta. */
    val version: Int? = null,
) {
    val completed: Boolean get() = status.equals("completed", ignoreCase = true)
    val cancelled: Boolean get() = status.equals("cancelled", ignoreCase = true)
    val tags: List<String> get() = categories
    /** The due in canonical form, as task reminder keys use it ("" without a due). */
    val dueKey: String get() = Occurrence.canonical(due)
}

@Serializable
data class TaskList(val connected: Boolean = false, val items: List<TaskItem> = emptyList())

@Serializable
data class CollectionSync(
    val state: String = "idle",
    @SerialName("last_sync_at") val lastSyncAt: String? = null,
    val error: String? = null,
)

/**
 * A CalDAV collection: a calendar, a task list, or both. `url` is
 * the API id. [deviceHidden] is this phone's own visibility override (null: follow the
 * account's `hidden`); it never reaches the server.
 */
/**
 * Who shares a calendar with this user (server `shared`): kind "group" (a group calendar,
 * calendar/groups.py) or "user" (another person's calendar, calendar/sharing.py).
 */
@Serializable
data class SharedInfo(
    val kind: String = "",
    val group: String? = null,
    val owner: String? = null,
    val access: String? = null,
    val level: String? = null,
    val editor: Boolean = false,
)

@Serializable
data class CalendarCollection(
    val url: String = "",
    val name: String = "",
    val color: String? = null,
    val events: Boolean = false,
    val tasks: Boolean = false,
    val error: String? = null,
    @SerialName("calendar_id") val calendarId: Long? = null,
    @SerialName("connector_id") val connectorId: Long? = null,
    val description: String = "",
    val components: List<String> = emptyList(),
    @SerialName("read_only") val readOnly: Boolean = false,
    @Serializable(with = IanaZoneSerializer::class) val tz: String? = null,
    val hidden: Boolean = false,
    val busy: Boolean = true,
    val notify: Boolean = true,
    @SerialName("sort_order") val sortOrder: Int = 0,
    @SerialName("local_only") val localOnly: Boolean = false,
    val sync: CollectionSync? = null,
    /** The colour is kept only in mailr because the server refused it. */
    @SerialName("color_local") val colorLocal: Boolean = false,
    /** New events here start with these: a list of minutes, or `{timed: [...], all_day: [...]}` (§7). */
    @SerialName("default_reminders") val defaultReminders: JsonElement? = null,
    /** A group calendar, or a calendar another person shares with this user (null: the user's own). */
    val shared: SharedInfo? = null,
    @kotlinx.serialization.Transient val deviceHidden: Boolean? = null,
) {
    /** Only the owner (or an admin, for a group calendar) renames or deletes a shared calendar. */
    val canRenameOrDelete: Boolean get() = !readOnly && shared == null

    /** Why [canRenameOrDelete] is false for a shared calendar, in plain words; null otherwise. */
    val ownerNote: String? get() = shared?.let {
        if (it.kind == "user") AppText.get(R.string.api_calendar_owner_note_user, it.owner ?: AppText.get(R.string.api_calendar_its_owner))
        else AppText.get(R.string.api_calendar_owner_note_group)
    }

    /** Shown on this phone: the device override, else the account setting. */
    val visible: Boolean get() = !(deviceHidden ?: hidden)

    /** The default reminders of a timed or an all-day event here, or null when the calendar has none. */
    fun defaultRemindersFor(allDay: Boolean): List<Int>? {
        fun ints(e: JsonElement?): List<Int>? = (e as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.contentOrNull?.toIntOrNull() }
        return when (val d = defaultReminders) {
            is JsonArray -> ints(d)
            is JsonObject -> ints(d[if (allDay) "all_day" else "timed"])
            else -> null
        }
    }
}

@Serializable
data class CalendarDefaults(val calendar: String? = null, @SerialName("task_list") val taskList: String? = null)

/** One reminder's shared state (GET /reminders/state). */
@Serializable
data class RemoteReminder(
    val key: String,
    val kind: String,
    val uid: String,
    val occurrence: String = "",
    val minutes: Int = 0,
    val state: String,
    @SerialName("snoozed_until") val snoozedUntil: String? = null,
    val oneoff: Boolean = false,
)

@Serializable
data class RemoteReminders(val items: List<RemoteReminder> = emptyList(), val next: String? = null)

/** What the server read from an email for a new task or event (POST /calendar/from-message; nothing is saved). */
@Serializable
data class FromMessageDraft(
    val title: String = "",
    val start: String? = null,
    val end: String? = null,
    @SerialName("all_day") val allDay: Boolean = false,
    val location: String? = null,
    val notes: String? = null,
    /** Tasks: an ISO date or date-time. */
    val due: String? = null,
)

@Serializable
data class FromMessageSuggestion(
    val kind: String = "task",
    @SerialName("message_id") val messageId: Long = 0,
    val source: String = "",
    val draft: FromMessageDraft = FromMessageDraft(),
)

/** One occurrence inside the delta window (§5). Override fields are null when they equal the master's. */
@Serializable
data class DeltaInstance(
    val occurrence: String,
    val start: String,
    val end: String? = null,
    @SerialName("all_day") val allDay: Boolean = false,
    @SerialName("is_override") val isOverride: Boolean = false,
    val cancelled: Boolean = false,
    val summary: String? = null,
    val location: String? = null,
    val status: String? = null,
)

@Serializable
data class DeletedCollection(@SerialName("calendar_id") val calendarId: Long? = null, val url: String = "")

@Serializable
data class DeletedObject(val id: Long? = null, val uid: String = "", val kind: String = "event")

@Serializable
data class DeltaWindow(val from: String? = null, val to: String? = null)

/** GET /calendar/delta. `objects` are events and tasks, told apart by `kind`. */
@Serializable
data class Delta(
    val cursor: Long = 0,
    val more: Boolean = false,
    val reset: Boolean = false,
    val collections: List<CalendarCollection> = emptyList(),
    @SerialName("deleted_collections") val deletedCollections: List<DeletedCollection> = emptyList(),
    val objects: List<JsonObject> = emptyList(),
    val deleted: List<DeletedObject> = emptyList(),
    val instances: Map<String, List<DeltaInstance>> = emptyMap(),
    val window: DeltaWindow? = null,
    @SerialName("server_time") val serverTime: String? = null,
)

@Serializable
data class SyncInfo(val state: String = "idle", @SerialName("last_sync_at") val lastSyncAt: String? = null)

/** GET /tasks with the new parameters (§6). */
@Serializable
data class TaskPage(
    val connected: Boolean = false,
    val items: List<TaskItem> = emptyList(),
    @SerialName("next_cursor") val nextCursor: String? = null,
    val sync: SyncInfo? = null,
)

/** GET /tasks/counts: open tasks per smart list, list URL and tag. */
@Serializable
data class TaskCounts(
    val smart: Map<String, Int> = emptyMap(),
    val lists: Map<String, Int> = emptyMap(),
    val tags: Map<String, Int> = emptyMap(),
)

/** A tag (CATEGORIES value); mailr keeps its colour and order. */
@Serializable
data class TagInfo(val name: String, val count: Int = 0, val color: String? = null, val order: Int = 0)

@Serializable
data class BulkFailure(val uid: String, val detail: String = "")

/** POST /tasks/bulk. */
@Serializable
data class BulkResult(val ok: List<String> = emptyList(), val failed: List<BulkFailure> = emptyList())

/**
 * Remind again, one setting for the phone and the web: every [everyMin] minutes,
 * at most [max] times, while nobody acts on the reminder. The server repeats only to the web; the
 * phone repeats on its own alarm with the same values.
 */
@Serializable
data class RepeatSettings(
    val enabled: Boolean = false,
    @SerialName("every_min") val everyMin: Int = 10,
    val max: Int = 3,
) {
    /** Minutes between repeats; 0 = off. */
    val minutes: Int get() = if (enabled) everyMin else 0
}

@Serializable
data class WebReminderSettings(val desktop: Boolean = true, val push: Boolean = false)

/**
 * GET /reminders/settings (§9). The phone plans with these values so its reminder keys match
 * the server's. An absent `default_timed_event` means 10; an explicit null means none.
 */
@Serializable
data class ReminderSettings(
    val events: Boolean = true,
    val tasks: Boolean = true,
    @SerialName("default_timed_event") val defaultTimedEvent: Int? = 10,
    @SerialName("default_all_day_event") val defaultAllDayEvent: Int? = null,
    @SerialName("default_task") val defaultTask: Boolean = true,
    @SerialName("morning_hour") val morningHour: Int = 9,
    val web: WebReminderSettings = WebReminderSettings(),
    /** A reminder that fires late is shown ("Missed: …"); false marks it fired without a notification. */
    val missed: Boolean = true,
    /** The web plays a sound. The phone leaves sound to its notification channels (system settings). */
    val sound: Boolean = false,
    /** Remind again; null from a server that does not have it (the phone's own choice applies). */
    val repeat: RepeatSettings? = null,
) {
    /**
     * The reminders a new event gets when its calendar has no defaults of its own (as the server's
     * `initial_reminders`): the timed or all-day default as a list; none, or event reminders off, is [].
     */
    fun eventDefaults(allDay: Boolean): List<Int> =
        if (!events) emptyList() else listOfNotNull(if (allDay) defaultAllDayEvent else defaultTimedEvent)
}

/** One imported or skipped item of POST /calendar/import. */
@Serializable
data class ImportedItem(val uid: String = "", val summary: String = "", val reason: String? = null, val detail: String? = null)

/** POST /calendar/import (§7). */
@Serializable
data class ImportResult(
    val calendar: String? = null,
    @SerialName("calendar_id") val calendarId: Long? = null,
    val imported: List<ImportedItem> = emptyList(),
    val skipped: List<ImportedItem> = emptyList(),
    val failed: List<ImportedItem> = emptyList(),
)

/** One search hit (§8): an event (its next upcoming occurrence, else the last) or a task. */
data class SearchHit(val kind: String, val rank: Double, val event: CalendarEvent?, val task: TaskItem?)

data class SearchPage(val items: List<SearchHit>, val nextCursor: String?)

@Serializable
data class BusyInterval(val start: String, val end: String)

/** GET /calendar/freebusy: merged busy and tentative time (UTC), all-day busy days (dates, end exclusive). */
@Serializable
data class FreeBusy(
    val from: String = "",
    val to: String = "",
    val busy: List<BusyInterval> = emptyList(),
    val tentative: List<BusyInterval> = emptyList(),
    @SerialName("all_day") val allDay: List<BusyInterval> = emptyList(),
)

@Serializable
data class WorkingHours(val start: String = "09:00", val end: String = "17:30", val days: List<Int> = listOf(1, 2, 3, 4, 5))

/** POST /calendar/find-time. Days are ISO weekdays (1 = Monday). */
@Serializable
data class FindTimeRequest(
    val duration: Int,
    val from: String,
    val to: String,
    @Serializable(with = IanaZoneSerializer::class) val tz: String? = null,
    @SerialName("working_hours") val workingHours: WorkingHours? = null,
    @SerialName("buffer_minutes") val bufferMinutes: Int = 0,
    val granularity: Int = 15,
    val limit: Int = 10,
    @SerialName("avoid_all_day") val avoidAllDay: Boolean = true,
)

@Serializable
data class TimeSlot(val start: String, val end: String, val score: Double = 0.0)

@Serializable
data class FindTimeResult(val slots: List<TimeSlot> = emptyList(), @Serializable(with = IanaZoneSerializer::class) val tz: String? = null)

@Serializable
data class CollectionSyncStatus(
    @SerialName("calendar_id") val calendarId: Long? = null,
    val url: String = "",
    val name: String = "",
    val state: String = "idle",
    val error: String? = null,
    @SerialName("last_sync_at") val lastSyncAt: String? = null,
    @SerialName("next_sync_at") val nextSyncAt: String? = null,
)

@Serializable
data class ConnectorSyncStatus(
    val id: Long = 0,
    val name: String = "",
    val status: String = "",
    val detail: String? = null,
    /** "auth": the calendar server refused the password; Retry cannot help. */
    @SerialName("error_kind") val errorKind: String? = null,
    @SerialName("last_sync_at") val lastSyncAt: String? = null,
    val collections: List<CollectionSyncStatus> = emptyList(),
)

/** GET /calendar/sync-status. `state`: idle, syncing, initial, error or backoff. */
@Serializable
data class SyncStatus(
    val state: String = "idle",
    @SerialName("last_sync_at") val lastSyncAt: String? = null,
    val connectors: List<ConnectorSyncStatus> = emptyList(),
)

/** Calendar and task endpoints. Mutations take the caller's idempotency key (offline replay). */
class CalendarApi(val client: ApiClient) {
    suspend fun agenda(from: String, to: String) =
        client.get("calendar/agenda", Agenda.serializer(), mapOf("from" to from, "to" to to))

    suspend fun event(uid: String) = client.get("calendar/events/$uid", CalendarEvent.serializer())

    suspend fun tasks(status: String = "open") = client.get("tasks", TaskList.serializer(), mapOf("status" to status))

    suspend fun collections(refresh: Boolean = false) = client.get("calendar/calendars", ListSerializer(CalendarCollection.serializer()),
        if (refresh) mapOf("refresh" to 1) else emptyMap())

    suspend fun defaults() = client.get("calendar/defaults", CalendarDefaults.serializer())

    /** PUT /calendar/defaults: the default calendar and/or task list (signed-in user only). */
    suspend fun putDefaults(calendar: String? = null, taskList: String? = null) = client.send(
        "PUT", "calendar/defaults", CalendarDefaults.serializer(), json("calendar" to calendar, "task_list" to taskList),
        idempotencyKey = null,
    )

    /** PATCH /tasks/tags/{name}: colour and order (mailr-side). */
    suspend fun patchTag(name: String, color: String? = null, order: Int? = null) = client.send(
        "PATCH", "tasks/tags/$name", JsonObject.serializer(), json("color" to color, "order" to order), idempotencyKey = null,
    )

    /** Incremental sync (§5). A server before the calendar build answers 404. */
    suspend fun delta(cursor: Long, windowFrom: String?, windowTo: String?, limit: Int = 500) = client.get(
        "calendar/delta", Delta.serializer(),
        mapOf("cursor" to cursor, "window_from" to windowFrom, "window_to" to windowTo, "limit" to limit),
    )

    suspend fun syncStatus() = client.get("calendar/sync-status", SyncStatus.serializer())

    /** Pull to refresh on the server's side (§5). */
    suspend fun requestSync(connectorId: Long? = null, full: Boolean = false, wait: Boolean = false) = client.send(
        "POST", "calendar/sync", JsonObject.serializer(),
        json("connector_id" to connectorId, "full" to full, "wait" to wait), idempotencyKey = null,
    )

    /**
     * GET /tasks with the §6 filters. [smart] is today, overdue, upcoming, no_date, scheduled
     * or all; [sort] is manual, due, priority, created or title.
     */
    suspend fun taskPage(
        smart: String? = null, lists: List<String> = emptyList(), tags: List<String> = emptyList(), status: String? = null,
        q: String? = null, priority: Int? = null, parent: String? = null, includeFuture: Boolean = false,
        sort: String? = null, cursor: String? = null, limit: Int = 200,
    ): TaskPage {
        val query = buildString {
            lists.forEach { append("&list[]=").append(enc(it)) }
            tags.forEach { append("&tag[]=").append(enc(it)) }
        }
        return client.get("tasks" + if (query.isEmpty()) "" else "?" + query.drop(1), TaskPage.serializer(), mapOf(
            "smart" to smart, "status" to status, "q" to q, "priority" to priority, "parent" to parent,
            "include_future" to if (includeFuture) 1 else null, "sort" to sort, "cursor" to cursor, "limit" to limit))
    }

    suspend fun taskCounts() = client.get("tasks/counts", TaskCounts.serializer())

    suspend fun tags() = client.get("tasks/tags", ListSerializer(TagInfo.serializer()))

    /** POST /calendar/collections: [kind] is events, tasks or both. */
    suspend fun createCollection(name: String, color: String?, kind: String, connectorId: Long?) = client.send(
        "POST", "calendar/collections", CalendarCollection.serializer(),
        json("name" to name, "color" to color, "kind" to kind, "connector_id" to connectorId),
    )

    /** GET /tasks/{uid}: one task for a deep link (404 when it is gone or not visible). */
    suspend fun task(uid: String) = client.get("tasks/$uid", TaskItem.serializer())

    /** POST /calendar/import from an email: its calendar part, or the attachment [part]. */
    suspend fun importFromMessage(messageId: Long, part: Int?, calendar: String? = null) = client.send(
        "POST", "calendar/import", ImportResult.serializer(),
        json("message_id" to messageId, "part" to part, "calendar" to calendar),
    )

    suspend fun deleteCollection(calendarId: Long, confirm: String) = client.send(
        "DELETE", "calendar/collections/$calendarId", JsonElement.serializer(), null, query = mapOf("confirm" to confirm),
    )

    /** GET /calendar/search; `q` accepts `tag:x` and `list:x`. [kind] is event, task or all. */
    suspend fun search(q: String, kind: String = "all", from: String? = null, to: String? = null,
                       calendars: List<String> = emptyList(), limit: Int = 50, cursor: String? = null): SearchPage {
        val query = calendars.joinToString("") { "&calendar[]=" + enc(it) }
        val raw = client.get("calendar/search" + if (query.isEmpty()) "" else "?" + query.drop(1), JsonObject.serializer(),
            mapOf("q" to q, "kind" to kind, "from" to from, "to" to to, "limit" to limit, "cursor" to cursor))
        return parseSearch(raw)
    }

    suspend fun freebusy(from: String, to: String, calendars: List<String> = emptyList()): FreeBusy {
        val query = calendars.joinToString("") { "&calendar[]=" + enc(it) }
        return client.get("calendar/freebusy" + if (query.isEmpty()) "" else "?" + query.drop(1), FreeBusy.serializer(),
            mapOf("from" to from, "to" to to))
    }

    suspend fun findTime(req: FindTimeRequest) = client.send(
        "POST", "calendar/find-time", FindTimeResult.serializer(),
        MailrJson.encodeToString(FindTimeRequest.serializer(), req), idempotencyKey = null,
    )

    /** The raw object, so an explicit null (no default reminder) survives the cache. */
    suspend fun reminderSettings() = client.get("reminders/settings", JsonObject.serializer())

    suspend fun putReminderSettings(body: JsonObject) = client.send(
        "PUT", "reminders/settings", JsonObject.serializer(), MailrJson.encodeToString(JsonObject.serializer(), body),
    )

    /** ACT reminder.sync @android.notification: reminder states changed since the cursor. */
    suspend fun reminderStates(since: String?, kinds: String = "e,t") =
        client.get("reminders/state", RemoteReminders.serializer(), buildMap {
            if (since != null) put("since", since)
            // Note keys (n|) only when this app fires them (the Notes app is not installed).
            if (kinds != "e,t") put("kinds", kinds)
        })

    suspend fun rsvp(uid: String, response: String, key: String) = client.send(
        "POST", "calendar/events/$uid/rsvp", JsonObject.serializer(),
        MailrJson.encodeToString(JsonObject.serializer(), buildJsonObject { put("response", response) }), key,
    )

    /** ACT mail.to_task @android.reader, ACT mail.to_event @android.reader: a suggested task or event from one message ([kind] "task" or "event"). */
    suspend fun fromMessage(messageId: Long, kind: String) = client.send(
        "POST", "calendar/from-message", FromMessageSuggestion.serializer(),
        json("message_id" to messageId, "kind" to kind), idempotencyKey = null,
    )

    /** Saves the (edited) suggestion, linked to the message's conversation. Returns the created task or event. */
    suspend fun confirmFromMessage(body: String) =
        client.send("POST", "calendar/from-message/confirm", JsonObject.serializer(), body)

    companion object {
        /** Request bodies are built here and also stored verbatim in the offline queue. */
        /**
         * Create (with our UID) or patch. A patch sends the RRULE only when the user changed it.
         * The fields new in the calendar build are sent only when the draft sets them.
         */
        fun eventBody(e: EventDraft, create: Boolean, rruleChanged: Boolean = create): String =
            MailrJson.encodeToString(JsonObject.serializer(), buildJsonObject {
                if (create) put("uid", e.uid)
                if (create) e.conversationId?.let { put("conversation_id", it) }
                put("summary", e.summary)
                put("start", e.start)
                e.end?.let { put("end", it) }
                put("location", e.location)
                put("description", e.description)
                e.calendar?.let { put("calendar", it) }
                if (create) e.rrule?.let { put("recurrence", it) } else if (rruleChanged) put("recurrence", e.rrule ?: "")
                if (e.sendReminders != false) putJsonArray("reminders") { e.reminders.forEach { add(JsonPrimitive(it)) } }
                put("all_day", e.allDay)
                e.tz?.let { put("tz", it) }
                e.transparent?.let { put("transparent", it) }
                e.classification?.let { put("class", it) }
                e.color?.let { put("color", it) }
                e.categories?.let { cs -> putJsonArray("categories") { cs.forEach { add(JsonPrimitive(it)) } } }
                e.url?.let { put("url", it) }
                e.status?.let { put("status", it) }
                e.sendUpdates?.let { put("send_updates", it) }
                e.identityId?.let { put("identity_id", it) }
                e.attendees?.let { xs ->
                    putJsonArray("attendees") {
                        xs.forEach { a -> add(buildJsonObject { put("email", a.email); put("name", a.name); put("role", a.role) }) }
                    }
                }
            })

        /**
         * A task create or patch. On a patch, `parent` null clears the parent (as before) and
         * the fields new in the calendar build are left alone when null.
         */
        fun taskBody(t: TaskDraft, create: Boolean): String = MailrJson.encodeToString(JsonObject.serializer(), buildJsonObject {
            if (create) put("uid", t.uid)
            put("summary", t.summary)
            put("notes", t.notes)
            if (t.due != null) put("due", t.due) else if (!create) put("clear_due", true)
            if (create) { if (t.priority > 0) put("priority", t.priority) } else put("priority", t.priority)
            t.list?.let { put("list", it) }
            if (create) t.parent?.let { put("parent", it) } else put("parent", t.parent ?: "")
            putJsonArray("reminders") { t.reminders.forEach { add(JsonPrimitive(it)) } }
            t.remindersAt?.let { xs -> putJsonArray("reminders_at") { xs.forEach { add(JsonPrimitive(it)) } } }
            if (t.start != null) put("start", t.start) else if (!create && t.clearStart) put("clear_start", true)
            t.recurrence?.takeIf { !create || it.isNotEmpty() }?.let { put("recurrence", it) }
            t.repeatFrom?.let { put("repeat_from", it) }
            t.categories?.let { cs -> putJsonArray("categories") { cs.forEach { add(JsonPrimitive(it)) } } }
            t.sortOrder?.let { put("sort_order", it) }
            t.tz?.let { put("tz", it) }
            // The email a new task comes from (a commitment's Make a task); POST /tasks links it.
            if (create) t.conversationId?.let { put("conversation_id", it) }
        })

        fun json(vararg pairs: Pair<String, Any?>): String = MailrJson.encodeToString(JsonObject.serializer(), buildJsonObject {
            pairs.forEach { (k, v) ->
                when (v) {
                    null -> Unit
                    is Boolean -> put(k, v)
                    is Number -> put(k, v)
                    is JsonElement -> put(k, v)
                    is Collection<*> -> put(k, JsonArray(v.map { x -> if (x is Number) JsonPrimitive(x) else JsonPrimitive(x.toString()) }))
                    else -> put(k, v.toString())
                }
            }
        })

        fun parseSearch(raw: JsonObject): SearchPage {
            val items = (raw["items"] as? JsonArray).orEmpty().mapNotNull { el ->
                val o = el as? JsonObject ?: return@mapNotNull null
                val kind = (o["kind"] as? JsonPrimitive)?.contentOrNull ?: "event"
                val rank = (o["rank"] as? JsonPrimitive)?.doubleOrNull ?: 0.0
                if (kind == "task") catching { MailrJson.decodeFromJsonElement(TaskItem.serializer(), o) }.getOrNull()
                    ?.let { SearchHit(kind, rank, null, it) }
                else catching { MailrJson.decodeFromJsonElement(CalendarEvent.serializer(), o) }.getOrNull()
                    ?.let { SearchHit(kind, rank, it, null) }
            }
            return SearchPage(items, (raw["next_cursor"] as? JsonPrimitive)?.contentOrNull)
        }

        private fun enc(s: String) = java.net.URLEncoder.encode(s, "UTF-8")
    }
}

/** A guest as the event editor sends it. [role] is REQ-PARTICIPANT, OPT-PARTICIPANT or "". */
data class AttendeeInput(val email: String, val name: String = "", val role: String = "")

/**
 * What the event editor produces (create or edit). Times are ISO strings (dates for all-day).
 * The fields after [reminders] are new in the calendar build; null leaves them as they are.
 */
data class EventDraft(
    val uid: String,
    val summary: String,
    val start: String,
    val end: String?,
    val allDay: Boolean,
    val location: String = "",
    val description: String = "",
    val calendar: String? = null,
    val rrule: String? = null,
    val reminders: List<Int> = emptyList(),
    /** IANA zone the times are meant in (the server writes DTSTART;TZID). */
    @Serializable(with = IanaZoneSerializer::class) val tz: String? = null,
    /** Show as free. */
    val transparent: Boolean? = null,
    /** PUBLIC, PRIVATE or CONFIDENTIAL. */
    val classification: String? = null,
    val color: String? = null,
    val categories: List<String>? = null,
    val url: String? = null,
    /** CONFIRMED, TENTATIVE or CANCELLED. */
    val status: String? = null,
    val attendees: List<AttendeeInput>? = null,
    /** False writes the calendar only; no invitation mail goes out. */
    val sendUpdates: Boolean? = null,
    val identityId: Long? = null,
    /**
     * Whether [reminders] go in the request. false leaves them out: on a create the server writes
     * the default reminders (the calendar's, else the user's), on a patch the event keeps its own.
     * true sends them; [] then means "no reminders" and marks the event so no default fires.
     * null (the default): a create sends them; a patch sends them when they differ from the event's.
     */
    val sendReminders: Boolean? = null,
    /** On a create: the conversation the event is linked to ("Meeting with these people", mail.schedule_meeting). */
    val conversationId: Long? = null,
)

/**
 * What the task editor produces. `due` and `start` are ISO dates (no time) or date-times.
 * The fields after [reminders] are new in the calendar build; null leaves them as they are.
 */
data class TaskDraft(
    val uid: String,
    val summary: String,
    val notes: String = "",
    val due: String? = null,
    val priority: Int = 0,
    val list: String? = null,
    val parent: String? = null,
    val reminders: List<Int> = emptyList(),
    val start: String? = null,
    /** Remove the start date (patch only). */
    val clearStart: Boolean = false,
    /** RRULE without "RRULE:"; "" removes the repeat. */
    val recurrence: String? = null,
    /** "due" or "completion". */
    val repeatFrom: String? = null,
    /** Tags (CATEGORIES); the whole set. */
    val categories: List<String>? = null,
    val sortOrder: Long? = null,
    @Serializable(with = IanaZoneSerializer::class) val tz: String? = null,
    /** Absolute reminders (instants); null leaves them as they are, [] removes them. */
    val remindersAt: List<String>? = null,
    /** Create only: the conversation the task comes from (task.open_email). */
    val conversationId: Long? = null,
)

/** A task as a draft that changes nothing but what the caller overrides. */
fun TaskItem.toDraft(): TaskDraft = TaskDraft(uid, summary, notes.orEmpty(), due, priority, list, parent, reminders)
