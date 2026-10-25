package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.CalendarApi
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.live.LivePort
import io.github.murtazanasir.mailr.data.live.LiveSession
import io.github.murtazanasir.mailr.data.live.MailrLiveApi
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime

/*
 * The event and task editors under live editing (data/live/LiveSession). Each editor field is one
 * JSON value in the editor's own spelling, so the editor's copy, the base and the server's copy
 * (built into an editor state of its own) compare field by field. The event description and the
 * task notes merge by lines.
 */

private fun str(v: String?): JsonElement = v?.let { JsonPrimitive(it) } ?: JsonNull
private fun JsonElement?.text(): String? = (this as? JsonPrimitive)?.takeIf { it !is JsonNull }?.contentOrNull
private fun ints(xs: List<Int>) = JsonArray(xs.map { JsonPrimitive(it) })
private fun strs(xs: List<String>) = JsonArray(xs.map { JsonPrimitive(it) })
private fun JsonElement?.intList(): List<Int> = (this as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.intOrNull }.orEmpty()
private fun JsonElement?.strList(): List<String> = (this as? JsonArray)?.mapNotNull { it.text() }.orEmpty()

/** A date with an optional time: "2026-10-05" or "2026-10-05T09:00". */
private fun moment(d: LocalDate?, t: LocalTime?): String? = d?.let { if (t == null) it.toString() else "${it}T$t" }
private fun parseMoment(v: String?): Pair<LocalDate, LocalTime?>? = v?.let {
    catching { if (it.length == 10) LocalDate.parse(it) to null else LocalDateTime.parse(it).let { x -> x.toLocalDate() to x.toLocalTime() } }.getOrNull()
}

/** The server's copies decoded lately, by their fields, so a clean apply can rebase onto the item. */
private class Decoded<T> {
    private val map = LinkedHashMap<JsonObject, T>()
    operator fun get(k: JsonObject): T? = map[k]
    fun put(k: JsonObject, v: T) {
        map.remove(k)
        map[k] = v
        while (map.size > 6) map.remove(map.keys.first())
    }
}

/** The task editor as live editing sees it. [save] saves it now (Undo, "Use mine"). */
class TaskLivePort(private val s: TaskEditorState, private val save: () -> Unit) : LivePort {
    override val textFields = mapOf("notes" to "lines")
    private val decoded = Decoded<TaskItem>()

    /** The request body of the copy at the base version: a save sends only what differs from it (LiveSave). */
    var baseline: JsonObject? = body(s)

    override fun mine(): JsonObject = fieldsOf(s)
    override fun dirty(): Boolean = s.dirty

    override fun decode(server: JsonObject): JsonObject? {
        val o = s.original ?: return null
        val item = catching {
            val base = MailrJson.encodeToJsonElement(TaskItem.serializer(), o).jsonObject
            MailrJson.decodeFromJsonElement(TaskItem.serializer(), JsonObject(base + server + ("uid" to JsonPrimitive(o.uid))))
        }.getOrNull() ?: return null
        val f = fieldsOf(TaskEditorState(item, item.list, item.parent, s.uid))
        decoded.put(f, item)
        return f
    }

    override fun apply(content: JsonObject, clean: Boolean) {
        setFields(content)
        if (clean) {
            decoded[content]?.let { s.rebase(it) }
            s.markClean()
            baseline = body(s)
        }
    }

    /** The session took [content] as its base: a copy from the server sets the baseline. */
    fun rebased(content: JsonObject) {
        decoded[content]?.let { item -> baseline = body(TaskEditorState(item, item.list, item.parent, s.uid)) }
    }

    override fun saveNow() = save()

    override fun show(field: String, value: JsonElement?): String = when (field) {
        "categories" -> value.strList().joinToString(", ") { "@$it" }
        "reminders" -> value.intList().joinToString(", ") { AppText.plural(R.plurals.rem_span_minutes, it, it) }
        "rrule" -> value.text()?.let { Recurrence.parse(it)?.describe() } ?: AppText.get(R.string.cal_rec_does_not_repeat)
        else -> super.show(field, value)
    }

    private fun setFields(c: JsonObject) {
        c["summary"]?.let { s.title = it.text().orEmpty() }
        c["notes"]?.let { s.notes = it.text().orEmpty() }
        c["due"]?.let { val m = parseMoment(it.text()); s.dueDate = m?.first; s.dueTime = m?.second }
        c["start"]?.let { val m = parseMoment(it.text()); s.startDate = m?.first; s.startTime = m?.second }
        c["priority"]?.let { s.priority = (it as? JsonPrimitive)?.intOrNull ?: 0 }
        c["list"]?.let { s.list = it.text() }
        c["reminders"]?.let { s.reminders = it.intList() }
        c["rrule"]?.let { s.recurrence = Recurrence.parse(it.text()) }
        c["repeat_from"]?.let { s.repeatFrom = it.text() ?: "due" }
        c["categories"]?.let { s.tags = it.strList() }
    }

    companion object {
        fun fieldsOf(s: TaskEditorState): JsonObject = buildJsonObject {
            put("summary", s.title)
            put("notes", s.notes)
            put("due", str(moment(s.dueDate, s.dueTime)))
            put("start", str(moment(s.startDate, s.startTime)))
            put("priority", s.priority)
            put("list", str(s.list))
            put("reminders", ints(s.reminders))
            put("rrule", str(s.recurrence?.toRRule()))
            put("repeat_from", s.repeatFrom)
            put("categories", strs(s.tags))
        }

        fun body(s: TaskEditorState): JsonObject =
            MailrJson.parseToJsonElement(CalendarApi.taskBody(s.draft(), create = false)).jsonObject
    }
}

/**
 * The event editor as live editing sees it. A repeating event's editor shows one occurrence, so its
 * times are left out (the server's copy is the series). The event keeps its explicit Save: Undo and
 * "Use mine" put the copy back in the editor, and Save sends it.
 */
class EventLivePort(private val s: EventEditorState) : LivePort {
    override val textFields = mapOf("description" to "lines")
    private val decoded = Decoded<CalendarEvent>()
    private val timed get() = s.original?.recurring != true

    var baseline: JsonObject? = body(s)

    override fun mine(): JsonObject = fieldsOf(s, timed)
    override fun dirty(): Boolean = s.fieldsDirty

    override fun decode(server: JsonObject): JsonObject? {
        val o = s.original ?: return null
        val item = catching {
            val base = MailrJson.encodeToJsonElement(CalendarEvent.serializer(), o).jsonObject
            val theirs = if (timed) server else JsonObject(server - setOf("start", "end", "all_day", "tz"))
            MailrJson.decodeFromJsonElement(CalendarEvent.serializer(), JsonObject(base + theirs + ("uid" to JsonPrimitive(o.uid))))
        }.getOrNull() ?: return null
        val f = fieldsOf(stateOf(item), timed)
        decoded.put(f, item)
        return f
    }

    private fun stateOf(item: CalendarEvent) =
        EventEditorState(item, item, AppClock.localNow(), item.calendar, null, s.uid, displayZone = s.displayZone)

    override fun apply(content: JsonObject, clean: Boolean) {
        setFields(content)
        if (clean) {
            decoded[content]?.let { s.rebase(it) }
            s.markClean()
            baseline = body(s)
        }
    }

    fun rebased(content: JsonObject) {
        decoded[content]?.let { baseline = body(stateOf(it)) }
    }

    /** The event saves only with Save (guests may get mail): the editor keeps the copy, unsaved. */
    override fun saveNow() = Unit

    override fun show(field: String, value: JsonElement?): String = when (field) {
        "attendee_list" -> (value as? JsonArray)?.joinToString(", ") { (it as? JsonObject)?.get("email").text().orEmpty() }.orEmpty()
        "when" -> value.text()?.split(';')?.let { p -> if (p.getOrNull(0) == "true") AppText.get(R.string.cal_live_when_all_day, p.getOrNull(1).orEmpty(), p.getOrNull(3).orEmpty())
            else "${p.getOrNull(1)} ${p.getOrNull(2)} – ${p.getOrNull(3)} ${p.getOrNull(4)}" }.orEmpty()
        "rrule" -> value.text()?.let { Recurrence.parse(it)?.describe() } ?: AppText.get(R.string.cal_rec_does_not_repeat)
        "reminders" -> value.intList().joinToString(", ") { AppText.plural(R.plurals.rem_span_minutes, it, it) }
        else -> super.show(field, value)
    }

    private fun setFields(c: JsonObject) {
        c["summary"]?.let { s.title = it.text().orEmpty() }
        c["location"]?.let { s.location = it.text().orEmpty() }
        c["description"]?.let { s.notes = it.text().orEmpty() }
        c["when"]?.text()?.split(';')?.takeIf { it.size == 5 }?.let { p ->
            catching {
                s.allDay = p[0] == "true"
                s.startDate = LocalDate.parse(p[1]); s.startTime = LocalTime.parse(p[2])
                s.endDate = LocalDate.parse(p[3]); s.endTime = LocalTime.parse(p[4])
            }
        }
        c["rrule"]?.let { s.recurrence = Recurrence.parse(it.text()) }
        c["reminders"]?.let { s.reminders = it.intList() }
        c["attendee_list"]?.let { el ->
            val gs = (el as? JsonArray)?.mapNotNull { g ->
                val o = g as? JsonObject ?: return@mapNotNull null
                Guest(o["email"].text() ?: return@mapNotNull null, o["name"].text().orEmpty(), o["partstat"].text() ?: "NEEDS-ACTION",
                    (o["optional"] as? JsonPrimitive)?.booleanOrNull == true)
            }.orEmpty()
            s.guests.clear()
            s.guests.addAll(gs)
        }
        c["color"]?.let { s.color = it.text() }
        c["transparent"]?.let { s.free = (it as? JsonPrimitive)?.booleanOrNull == true }
        c["class"]?.let { s.private = (it as? JsonPrimitive)?.booleanOrNull == true }
        c["url"]?.let { s.video = it.text().orEmpty() }
        c["calendar"]?.let { v -> v.text()?.let { s.calendar = it } }
    }

    companion object {
        fun fieldsOf(s: EventEditorState, timed: Boolean): JsonObject = buildJsonObject {
            put("summary", s.title)
            put("location", s.location)
            put("description", s.notes)
            if (timed) put("when", "${s.allDay};${s.startDate};${s.startTime};${s.endDate};${s.endTime}")
            put("rrule", str(s.recurrence?.toRRule()))
            put("reminders", ints(s.reminders))
            put("attendee_list", JsonArray(s.guests.map { g ->
                buildJsonObject { put("email", g.email); put("name", g.name); put("partstat", g.partstat); put("optional", g.optional) }
            }))
            put("color", str(s.color))
            put("transparent", s.free)
            put("class", s.private)
            put("url", s.video)
            put("calendar", str(s.calendar))
        }

        fun body(s: EventEditorState): JsonObject =
            MailrJson.parseToJsonElement(CalendarApi.eventBody(s.draft(), create = false, rruleChanged = true)).jsonObject
    }
}

/**
 * A live session for an open editor: it follows the change stream while the editor is shown and
 * reads the item's current version when it opens. [rebased] hears each new base (the port's baseline).
 */
@Composable
fun rememberLiveSession(g: AppGraph, kind: String, id: String?, port: LivePort?, rebased: (JsonObject) -> Unit = {}): LiveSession? {
    val scope = rememberCoroutineScope()
    val session = remember(port, id) {
        if (port == null || id == null) null
        else LiveSession(kind, id, g.client.clientId, port, scope, MailrLiveApi(g.api), onBase = rebased)
    }
    LaunchedEffect(session) {
        val s = session ?: return@LaunchedEffect
        s.open()
        g.live.events.collect { s.onEvent(it) }
    }
    return session
}
