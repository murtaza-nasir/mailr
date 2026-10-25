package io.github.murtazanasir.mailr.notes

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import java.util.concurrent.CopyOnWriteArrayList

/**
 * A small mailr server for the Notes module's tests (MockWebServer): notes with items and sections,
 * labels, tables with rows, shares and revisions, as the notes and tables REST APIs answer them.
 * [offline] drops every connection; [conflictOn] answers the next item or row PATCH with a 409
 * conflict; [refuse] answers a path with a status; [tooOld] answers 426 as the server's
 * oldest-version floor does; [notesFloor] answers 426 to a Notes app User-Agent below it, compared as
 * core/appversion.py compares it; [releases] is the app release list. Every request is kept in [log].
 */
class FakeNotesServer {
    data class Req(val method: String, val path: String, val body: JsonObject?, val key: String?, val origin: String?,
                   val userAgent: String? = null)

    val server = MockWebServer()
    private val traceLog = CopyOnWriteArrayList<Pair<Long, String>>()
    private fun trace(s: String) { traceLog += System.currentTimeMillis() to s }
    /** The last requests answered, newest last, with how long ago (for a timed-out test's report). */
    fun recent(n: Int = 15): String {
        val now = System.currentTimeMillis()
        return traceLog.takeLast(n).joinToString("\n") { (t, s) -> "  -${now - t} ms $s" }
    }
    companion object {
        @Volatile var current: FakeNotesServer? = null
    }
    val log = CopyOnWriteArrayList<Req>()
    @Volatile var offline = false
    /** Answer the response of the next request to this path prefix, then drop the connection (the phone never hears). */
    @Volatile var loseReplyTo: String? = null
    val conflictOn = java.util.concurrent.ConcurrentHashMap<String, String>()
    val refuse = java.util.concurrent.ConcurrentHashMap<String, Int>()
    /** The server's floor refuses this app version: every API call but /api/v1/app/ answers 426. */
    @Volatile var tooOld = false
    /**
     * The instance setting `phone_apps.notes_min_version` ("1.1.0"): a request whose User-Agent is the
     * Notes app below it gets the server's 426. Null or empty: no floor.
     */
    @Volatile var notesFloor: String? = null
    /** The body of GET /api/v1/app/releases (core/phone_apps.releases); null answers 404. */
    @Volatile var releases: String? = null

    data class N(var id: String, var kind: String, var title: String, var body: String = "", var color: String? = null,
                 var version: Long = 1, var level: String = "owner", var deleted: String? = null, var pinned: Boolean = false,
                 var archived: Boolean = false, var labels: List<String> = emptyList(), var shopping: Boolean = false,
                 var ownerName: String = "Me Example", var reminders: List<String> = emptyList(),
                 var links: List<Long> = emptyList(),
                 /** Local models only, and whether this viewer may turn it off (the owner only). */
                 var localOnly: Boolean = false, var canLowerLocalOnly: Boolean = true,
                 /** The repeat rule and the next reset the server planned. */
                 var repeat: String? = null, var repeatNext: String? = null,
                 var sharedCount: Int? = null, var suggestUsual: Boolean = false)

    data class I(val id: String, var text: String, var sectionId: String? = null, var checked: Boolean = false,
                 var qty: JsonElement = JsonNull, var extra: String? = null, var version: Long = 1, var order: String,
                 var checkedAt: String? = null)

    data class S(val id: String, var name: String, var order: String)

    data class T(val id: String, var name: String, var columns: List<JsonObject>, var level: String = "owner",
                 var deleted: String? = null, var version: Long = 1, var color: String? = null,
                 /** The default view's sort and filters ({"sort": [...], "filters": [...]}); GET rows does not apply them. */
                 var viewSpec: JsonObject? = null,
                 var localOnly: Boolean = false, var canLowerLocalOnly: Boolean = true)

    data class R(val id: String, val table: String, var cells: JsonObject, var version: Long = 1, var order: String, var deleted: Boolean = false)

    val notes = LinkedHashMap<String, N>()
    val items = LinkedHashMap<String, MutableList<I>>()
    val sections = LinkedHashMap<String, MutableList<S>>()
    val labels = mutableListOf<String>()
    val tables = LinkedHashMap<String, T>()
    val rows = LinkedHashMap<String, R>()
    val shares = LinkedHashMap<String, MutableList<JsonObject>>()
    /** What GET /reminders/state answers (items). */
    val stateItems = CopyOnWriteArrayList<JsonObject>()
    val taskOf = java.util.concurrent.ConcurrentHashMap<String, String>()
    /** Presence signals received, in order: "note:<id>:here:hello", "note:<id>:left". */
    val presence = CopyOnWriteArrayList<String>()
    /** What GET /notes/{id}/usual answers (the list's usual items); one "coffee" when empty. */
    val usual = CopyOnWriteArrayList<String>()
    /** The user's own templates (id → name); the built-in "Packing" starter is always listed. */
    val templates = LinkedHashMap<String, String>()
    private var next = 100
    private var order = 0

    fun note(kind: String, title: String, items: List<String> = emptyList(), sections: List<String> = emptyList()): N {
        val n = N("n${next++}", kind, title)
        notes[n.id] = n
        this.sections[n.id] = sections.map { S("s-${next++}", it, "s${order++}") }.toMutableList()
        this.items[n.id] = items.map { I(java.util.UUID.randomUUID().toString(), it, order = "a${order++}") }.toMutableList()
        return n
    }

    fun table(name: String, columns: List<Pair<String, String>>, rows: List<Map<String, String>> = emptyList()): T {
        val t = T("t${next++}", name, columns.mapIndexed { i, (n, type) ->
            buildJsonObject { put("id", "c$i"); put("name", n); put("type", type); put("order", "c$i") }
        })
        tables[t.id] = t
        rows.forEach { cells ->
            val r = R(java.util.UUID.randomUUID().toString(), t.id, JsonObject(cells.mapValues { JsonPrimitive(it.value) }), order = "r${order++}")
            this.rows[r.id] = r
        }
        return t
    }

    fun start(): FakeNotesServer {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val t0 = System.currentTimeMillis()
                val r = synchronized(this@FakeNotesServer) { handle(request) }
                trace("${request.method} ${request.path} seq=${request.sequenceNumber} waited=${System.currentTimeMillis() - t0}ms")
                return r
            }
            // The socket policy of a refused connection is read before the request is: offline drops it at once.
            override fun peek(): MockResponse =
                if (offline) MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START) else super.peek()
        }
        server.start()
        current = this
        return this
    }

    private fun ok(o: JsonElement) = MockResponse().setResponseCode(200).setHeader("Content-Type", "application/json").setBody(o.toString())
    private fun err(code: Int, detail: String) = MockResponse().setResponseCode(code).setBody("""{"detail":"$detail"}""")

    fun noteJson(n: N, full: Boolean = true): JsonObject = buildJsonObject {
        put("id", n.id); put("kind", n.kind); put("title", n.title); put("body", n.body)
        n.color?.let { put("color", it) }
        put("shopping", n.shopping); put("version", n.version); put("level", n.level)
        put("local_only", n.localOnly); put("can_lower_local_only", n.canLowerLocalOnly)
        put("repeat", n.repeat?.let { r -> buildJsonObject { put("rule", r); put("next", n.repeatNext?.let(::JsonPrimitive) ?: JsonNull); put("last", JsonNull) } } ?: JsonNull)
        put("can_delete", n.level == "owner"); put("checked_at_bottom", true); put("suggest_usual", n.suggestUsual)
        put("updated_at", "2026-10-05T10:00:00+00:00")
        n.deleted?.let { put("deleted_at", it) }
        put("owner", buildJsonObject { put("kind", "user"); put("id", if (n.level == "owner") 1 else 2); put("name", n.ownerName) })
        put("state", buildJsonObject {
            put("pinned", n.pinned); put("archived", n.archived); put("labels", JsonArray(n.labels.map(::JsonPrimitive)))
        })
        put("sharing", buildJsonObject { put("count", n.sharedCount ?: shares[n.id]?.size ?: 0) })
        put("links", JsonArray(n.links.map { id -> buildJsonObject { put("id", id); put("target_kind", "conversation")
            put("target_ref", buildJsonObject { put("conversation_id", 3066) }); put("title", "Re: Q4 plan") } }))
        put("reminders", JsonArray(n.reminders.map { at -> buildJsonObject { put("key", "n|${n.id}|abs|$at"); put("at", at); put("state", "upcoming") } }))
        if (full) {
            put("sections", JsonArray(sections[n.id].orEmpty().map { s -> buildJsonObject { put("id", s.id); put("name", s.name); put("order", s.order) } }))
            put("items", JsonArray(items[n.id].orEmpty().map { itemJson(it) }))
        }
    }

    fun itemJson(i: I) = buildJsonObject {
        put("id", i.id); put("text", i.text); put("key", i.text.lowercase()); put("checked", i.checked); put("order", i.order)
        put("section_id", i.sectionId?.let(::JsonPrimitive) ?: JsonNull); put("qty", i.qty); put("version", i.version)
        i.extra?.let { put("extra", it) }
        i.checkedAt?.let { put("checked_at", it) }
        taskOf[i.id]?.let { put("task_uid", it) }
        put("added_by", buildJsonObject { put("id", 1); put("name", "Me Example") })
    }

    fun tableJson(t: T) = buildJsonObject {
        put("id", t.id); put("kind", "table"); put("name", t.name); put("version", t.version); put("level", t.level)
        put("can_delete", t.level == "owner"); put("primary_column", "c0")
        put("local_only", t.localOnly); put("can_lower_local_only", t.canLowerLocalOnly)
        t.color?.let { put("color", it) }
        t.deleted?.let { put("deleted_at", it) }
        put("columns", JsonArray(t.columns))
        put("views", JsonArray(listOf(buildJsonObject {
            put("id", "v1"); put("name", "All"); put("default", true)
            put("spec", buildJsonObject {
                put("phone_columns", JsonArray(listOf(JsonPrimitive("c1"))))
                t.viewSpec?.forEach { (k, v) -> put(k, v) }
            })
        })))
        put("counts", buildJsonObject { put("rows", rows.values.count { it.table == t.id && !it.deleted }); put("columns", t.columns.size) })
    }

    fun rowJson(r: R) = buildJsonObject {
        put("id", r.id); put("cells", r.cells); put("order", r.order); put("version", r.version); put("table", r.table)
    }

    /** core/appversion.py too_old(ua, floor, "notes"): the Notes app's version below the floor. */
    private fun belowFloor(ua: String, floor: String): Boolean {
        fun parts(m: MatchResult) = listOf(m.groupValues[1].toInt(), m.groupValues[2].toInt(), m.groupValues[3].ifEmpty { "0" }.toInt())
        val v = Regex("""\bmailr-android-notes/(\d+)\.(\d+)(?:\.(\d+))?""").find(ua) ?: return false
        val f = Regex("""^\s*(\d+)\.(\d+)(?:\.(\d+))?\s*$""").find(floor) ?: return false
        val a = parts(v)
        val b = parts(f)
        for (i in 0..2) if (a[i] != b[i]) return a[i] < b[i]
        return false
    }

    private fun undo(method: String, path: String) = buildJsonObject { put("method", method); put("path", path); put("label", "Undo") }

    private fun handle(req: RecordedRequest): MockResponse {
        // A kept-alive connection reaches dispatch() even offline: drop it after the request.
        if (offline) return MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST)
        val full = req.path.orEmpty()
        val path = full.substringBefore('?').removePrefix("/api/v1/")
        val query = full.substringAfter('?', "")
        val text = req.body.readUtf8()
        val body = runCatching { kotlinx.serialization.json.Json.parseToJsonElement(text).jsonObject }.getOrNull()
        log += Req(req.method ?: "", path + (if (query.isNotEmpty()) "?$query" else ""), body, req.getHeader("Idempotency-Key"),
            req.getHeader("X-Mailr-Client"), req.getHeader("User-Agent"))
        if (path == "app/releases") return releases?.let { MockResponse().setResponseCode(200).setHeader("Content-Type", "application/json").setBody(it) }
            ?: err(404, "No route")
        if (tooOld) return err(426, "This version of the Mailr app is no longer supported. Update it to 9.0.0 or later.")
        notesFloor?.takeIf { it.isNotBlank() && path != "health" }?.let { floor ->
            if (belowFloor(req.getHeader("User-Agent").orEmpty(), floor))
                return err(426, "This version of the Notes app is no longer supported. Update it to $floor or later.")
        }
        refuse.entries.firstOrNull { path.startsWith(it.key) && req.method != "GET" }?.let { return err(it.value, "Refused here") }
        val lose = loseReplyTo
        val res = route(req.method ?: "GET", path, query, body)
        if (lose != null && path.startsWith(lose) && req.method != "GET") {
            loseReplyTo = null
            return res.setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST)
        }
        return res
    }

    private fun s(o: JsonObject?, k: String) = (o?.get(k) as? JsonPrimitive)?.contentOrNull

    private fun route(m: String, path: String, query: String, b: JsonObject?): MockResponse {
        val p = path.split('/')
        when {
            m == "GET" && path == "notes/sync" -> return ok(buildJsonObject {
                put("notes", JsonArray(notes.values.filter { it.deleted == null }.map { noteJson(it) }))
                put("trashed", JsonArray(notes.values.filter { it.deleted != null }.map { JsonPrimitive(it.id) }))
                put("visible", buildJsonObject { notes.values.filter { it.deleted == null }.forEach { put(it.id, it.level) } })
                put("now", "2026-10-05T10:00:0${next % 10}+00:00")
            })
            m == "GET" && path == "tables/sync" -> return ok(buildJsonObject {
                put("tables", JsonArray(tables.values.filter { it.deleted == null }.map { tableJson(it) }))
                put("rows", JsonArray(rows.values.filter { !it.deleted && tables[it.table]?.deleted == null }.map { rowJson(it) }))
                put("deleted_rows", JsonArray(rows.values.filter { it.deleted }.map { buildJsonObject { put("table", it.table); put("id", it.id) } }))
                put("trashed", JsonArray(tables.values.filter { it.deleted != null }.map { JsonPrimitive(it.id) }))
                put("visible", buildJsonObject { tables.values.filter { it.deleted == null }.forEach { put(it.id, it.level) } })
                put("now", "2026-10-05T10:00:00+00:00")
            })
            m == "GET" && path == "reminders/state" -> return ok(buildJsonObject {
                put("items", JsonArray(stateItems.toList())); put("next", JsonNull)
            })
            m == "GET" && path == "notes/templates" -> return ok(buildJsonObject {
                put("templates", JsonArray(listOf(buildJsonObject { put("id", "b-packing"); put("kind", "checklist"); put("name", "Packing"); put("builtin", true) }) +
                    templates.map { (id, name) -> buildJsonObject { put("id", id); put("kind", "text"); put("name", name); put("builtin", false) } }))
            })
            m == "DELETE" && path.startsWith("notes/templates/") -> {
                templates.remove(path.removePrefix("notes/templates/"))
                return ok(buildJsonObject { put("ok", true) })
            }
            m == "POST" && path == "notes/from-template" -> {
                val n = note("checklist", "Packing", listOf("socks", "charger"))
                return ok(buildJsonObject { put("note", noteJson(n)); put("undo", undo("DELETE", "/notes/${n.id}")) })
            }
            m == "GET" && path == "notes/labels" -> return ok(buildJsonObject {
                put("labels", JsonArray(labels.map { buildJsonObject { put("name", it) } }))
            })
            m == "POST" && path == "notes/labels" -> { labels += s(b, "name")!!; return ok(buildJsonObject { put("undo", JsonNull) }) }
            path.startsWith("notes/labels/") && (m == "PATCH" || m == "DELETE") -> {
                val name = java.net.URLDecoder.decode(path.removePrefix("notes/labels/"), "UTF-8")
                val i = labels.indexOf(name)
                if (m == "DELETE") labels.remove(name) else s(b, "name")?.let { if (i >= 0) labels[i] = it }
                return ok(buildJsonObject { put("ok", true) })
            }
            m == "GET" && path == "notes/search" -> return ok(buildJsonObject {
                val q = query.substringAfter("q=").lowercase()
                put("notes", JsonArray(notes.values.filter { n -> items[n.id].orEmpty().any { it.text.lowercase().contains(q) } }.map { noteJson(it, false) }))
                put("tables", JsonArray(emptyList()))
            })
            m == "POST" && path == "notes" -> {
                val n = note(s(b, "kind") ?: "text", s(b, "title").orEmpty())
                n.body = s(b, "body").orEmpty()
                s(b, "lines")?.split(',', '\n')?.map { it.trim() }?.filter { it.isNotEmpty() }?.forEach {
                    items[n.id]!!.add(I(java.util.UUID.randomUUID().toString(), it, order = "a${order++}"))
                }
                return ok(buildJsonObject { put("note", noteJson(n)); put("undo", undo("DELETE", "/notes/${n.id}")) })
            }
            m == "POST" && path == "tables" -> {
                val cols = if (s(b, "template") == "log") listOf("Date" to "date", "Item" to "text", "Amount" to "number") else listOf("Name" to "text")
                val t = table(s(b, "name").orEmpty(), cols)
                return ok(buildJsonObject { put("table", tableJson(t)); put("undo", undo("DELETE", "/tables/${t.id}")) })
            }
            p[0] == "notes" && p.size >= 2 -> {
                val n = notes[p[1]] ?: return err(404, "Not found")
                return noteRoute(m, n, p.drop(2), query, b)
            }
            p[0] == "tables" && p.size >= 2 -> {
                val t = tables[p[1]] ?: return err(404, "Not found")
                return tableRoute(m, t, p.drop(2), b, query)
            }
        }
        return err(404, "Not found")
    }

    private fun noteRoute(m: String, n: N, rest: List<String>, query: String, b: JsonObject?): MockResponse {
        val list = items.getOrPut(n.id) { mutableListOf() }
        when {
            rest.isEmpty() && m == "GET" -> return ok(noteJson(n))
            rest.isEmpty() && m == "PATCH" -> {
                conflictOn.remove(n.id)?.let { theirs -> return conflict("note", n.id, n.version, buildJsonObject { put("title", theirs) }) }
                val lo = b?.get("local_only")?.jsonPrimitive?.booleanOrNull
                if (lo == false && n.localOnly && !n.canLowerLocalOnly) return err(403, "Only the owner can turn Local models only off")
                lo?.let { n.localOnly = it }
                b?.get("suggest_usual")?.jsonPrimitive?.booleanOrNull?.let { n.suggestUsual = it }
                if (b?.containsKey("repeat") == true) {
                    val r = s(b, "repeat")
                    if (r != null && n.kind != "checklist") return err(422, "Only a list repeats")
                    n.repeat = r; n.repeatNext = r?.let { "2026-10-11T04:00:00+00:00" }
                }
                s(b, "title")?.let { n.title = it }
                s(b, "body")?.let { n.body = it }
                if (b?.containsKey("color") == true) n.color = s(b, "color")?.ifEmpty { null }
                n.version++
                return ok(buildJsonObject { put("note", noteJson(n)); put("undo", undo("POST", "/notes/${n.id}/revert")) })
            }
            rest.isEmpty() && m == "DELETE" -> {
                if (query.contains("forever=true")) { notes.remove(n.id); return ok(buildJsonObject { put("ok", true); put("undo", JsonNull) }) }
                n.deleted = io.github.murtazanasir.mailr.time.AppClock.now(java.time.ZoneOffset.UTC).toOffsetDateTime().toString()  // now, so "Deleted today" holds on any day
                return ok(buildJsonObject { put("ok", true); put("undo", undo("POST", "/notes/${n.id}/restore")) })
            }
            rest == listOf("restore") -> { n.deleted = null; return ok(buildJsonObject { put("note", noteJson(n)) }) }
            rest == listOf("presence") && m == "POST" -> {
                presence += "note:${n.id}:${s(b, "state")}" + if (b?.get("hello")?.jsonPrimitive?.booleanOrNull == true) ":hello" else ""
                return ok(buildJsonObject { put("ok", true); put("heartbeat", 15); put("ttl", 40); put("others", 1) })
            }
            rest == listOf("export") && m == "GET" -> return MockResponse().setResponseCode(200).setHeader("Content-Type", "text/markdown")
                .setBody("# ${n.title}\n\n${n.body}" + list.joinToString("") { "\n- [${if (it.checked) "x" else " "}] ${it.text}" })
            rest == listOf("save-template") && m == "POST" -> {
                val id = "tpl${next++}"
                templates[id] = s(b, "name") ?: n.title
                return ok(buildJsonObject { put("template", buildJsonObject { put("id", id); put("name", templates[id]!!) }) })
            }
            rest == listOf("reminders") && m == "PUT" -> {
                n.reminders = b!!["at"]!!.jsonArray.map { it.jsonPrimitive.content }
                return ok(buildJsonObject { put("reminders", JsonArray(emptyList())); put("undo", undo("PUT", "/notes/${n.id}/reminders")) })
            }
            rest == listOf("state") && m == "PUT" -> {
                b?.get("pinned")?.jsonPrimitive?.booleanOrNull?.let { n.pinned = it }
                b?.get("archived")?.jsonPrimitive?.booleanOrNull?.let { n.archived = it }
                (b?.get("labels") as? JsonArray)?.let { a -> n.labels = a.map { it.jsonPrimitive.content } }
                return ok(buildJsonObject { put("state", buildJsonObject { put("pinned", n.pinned) }) })
            }
            rest == listOf("convert") -> {
                n.kind = s(b, "to")!!
                if (n.kind == "text") { n.body = list.joinToString("\n") { "- [ ] " + it.text }; list.clear() }
                n.version++
                return ok(buildJsonObject { put("note", noteJson(n)) })
            }
            rest == listOf("items") && m == "POST" -> {
                val added = mutableListOf<I>()
                (b?.get("items") as? JsonArray)?.forEach { e ->
                    val o = e.jsonObject
                    val id = s(o, "id") ?: java.util.UUID.randomUUID().toString()
                    if (list.any { it.id == id }) return@forEach
                    val same = list.firstOrNull { it.text.equals(s(o, "text"), true) && !it.checked }
                    if (same != null) { same.version++; return@forEach }
                    val sid = s(o, "section_id")?.takeIf { sid -> sections[n.id].orEmpty().any { it.id == sid } }
                    I(id, s(o, "text")!!, sid, o["checked"]?.jsonPrimitive?.booleanOrNull ?: false, o["qty"] ?: JsonNull, order = "a${order++}")
                        .also { list += it; added += it }
                }
                (b?.get("usual") as? JsonArray)?.forEach { k ->
                    I(java.util.UUID.randomUUID().toString(), k.jsonPrimitive.content, order = "a${order++}").also { list += it; added += it }
                }
                n.version++
                return ok(buildJsonObject {
                    put("items", JsonArray(added.map { itemJson(it) })); put("merged", JsonArray(emptyList()))
                    put("undo", undo("POST", "/notes/${n.id}/revert"))
                })
            }
            rest.size == 2 && rest[0] == "items" && m == "PATCH" -> {
                val i = list.firstOrNull { it.id == rest[1] } ?: return err(404, "No item")
                conflictOn.remove(i.id)?.let { theirs ->
                    i.text = theirs; i.version++
                    return conflict("checkitem", i.id, i.version, itemJson(i))
                }
                b?.get("checked")?.jsonPrimitive?.booleanOrNull?.let { i.checked = it; i.checkedAt = s(b, "device_at") }
                s(b, "text")?.let { i.text = it.replace(Regex("\\s*x(\\d+)$"), "") }
                if (b?.containsKey("qty") == true) i.qty = b["qty"]!!
                s(b, "extra")?.let { i.extra = it.ifEmpty { null } }
                i.version++
                return ok(buildJsonObject { put("item", itemJson(i)); put("ignored", false); put("undo", undo("PATCH", "/notes/${n.id}/items/${i.id}")) })
            }
            rest.size == 2 && rest[0] == "items" && m == "DELETE" -> {
                list.removeAll { it.id == rest[1] }
                return ok(buildJsonObject { put("ok", true); put("undo", undo("POST", "/notes/${n.id}/revert")) })
            }
            rest.size == 3 && rest[0] == "items" && rest[2] == "move" -> {
                val i = list.firstOrNull { it.id == rest[1] } ?: return err(404, "No item")
                if (b?.containsKey("section_id") == true) i.sectionId = s(b, "section_id")
                return ok(buildJsonObject { put("item", itemJson(i)) })
            }
            rest == listOf("items", "clear-checked") -> { list.removeAll { it.checked }; return ok(buildJsonObject { put("count", 1) }) }
            rest == listOf("items", "uncheck-all") -> { list.forEach { it.checked = false }; return ok(buildJsonObject { put("count", 1) }) }
            rest == listOf("sections") && m == "POST" -> {
                val sec = S("s-${next++}", s(b, "name")!!, "s${order++}")
                sections.getOrPut(n.id) { mutableListOf() } += sec
                return ok(buildJsonObject { put("section", buildJsonObject { put("id", sec.id); put("name", sec.name); put("order", sec.order) }) })
            }
            rest.size == 2 && rest[0] == "sections" && m == "DELETE" -> {
                sections[n.id]?.removeAll { it.id == rest[1] }
                list.filter { it.sectionId == rest[1] }.forEach { if (query.contains("items=delete")) list.remove(it) else it.sectionId = null }
                return ok(buildJsonObject { put("ok", true) })
            }
            rest.size == 3 && rest[0] == "items" && rest[2] == "to-task" -> {
                list.firstOrNull { it.id == rest[1] }?.let { taskOf[it.id] = "task-1" }
                return ok(buildJsonObject { put("task_uid", "task-1") })
            }
            rest.size == 2 && rest[0] == "sections" && m == "PATCH" -> {
                sections[n.id]?.firstOrNull { it.id == rest[1] }?.let { sec -> s(b, "name")?.let { sec.name = it } }
                return ok(buildJsonObject { put("section", buildJsonObject { put("id", rest[1]) }) })
            }
            rest.size == 2 && rest[0] == "links" && m == "DELETE" -> {
                n.links = n.links.filter { it.toString() != rest[1] }
                return ok(buildJsonObject { put("ok", true) })
            }
            rest == listOf("usual") -> return ok(buildJsonObject {
                put("enabled", true)
                put("items", JsonArray(usual.ifEmpty { listOf("coffee") }.map { u -> buildJsonObject { put("key", u); put("text", u); put("count", 4) } }))
            })
            rest == listOf("revisions") -> return ok(buildJsonObject {
                put("revisions", JsonArray(listOf(
                    buildJsonObject { put("rev", 2); put("at", "2026-10-05T09:00:00+00:00"); put("source", "edit"); put("summary", "+1 item")
                        put("by", buildJsonObject { put("name", "Dana Example") }) },
                    buildJsonObject { put("rev", 1); put("at", "2026-10-04T09:00:00+00:00"); put("source", "edit"); put("summary", "Created") },
                )))
            })
            rest.size == 2 && rest[0] == "revisions" && m == "GET" -> return ok(buildJsonObject {
                put("rev", rest[1].toLong()); put("at", "2026-10-04T09:00:00+00:00")
                put("content", buildJsonObject { put("title", "Old title"); put("body", "old body") })
            })
            rest.size == 3 && rest[0] == "revisions" && rest[2] == "restore" -> {
                n.title = "Old title"; n.version++
                return ok(buildJsonObject { put("note", noteJson(n)); put("undo", undo("POST", "/notes/${n.id}/revisions/9/restore")) })
            }
            rest == listOf("revisions", "checkpoint") -> return ok(buildJsonObject { put("rev", JsonNull) })
            rest == listOf("shares") -> return ok(shareDialog(n.id, n.title))
            rest == listOf("shares", "me") && m == "DELETE" -> {
                notes.remove(n.id)
                return ok(buildJsonObject { put("ok", true); put("undo", undo("POST", "/notes/${n.id}/shares/me")) })
            }
            rest.size == 2 && rest[0] == "shares" && m == "PUT" -> {
                val list2 = shares.getOrPut(n.id) { mutableListOf() }
                list2.removeAll { s(it, "id") == rest[1] }
                list2 += buildJsonObject { put("kind", "user"); put("id", rest[1].toLong()); put("name", "Sam Example"); put("level", s(b, "level")!!) }
                return ok(shareDialog(n.id, n.title))
            }
            rest.size == 2 && rest[0] == "shares" && m == "DELETE" -> {
                shares[n.id]?.removeAll { s(it, "id") == rest[1] }
                return ok(shareDialog(n.id, n.title))
            }
        }
        return err(404, "No route")
    }

    fun shareDialog(id: String, name: String) = buildJsonObject {
        put("id", id); put("kind", "note"); put("name", name); put("level", "owner"); put("own", true); put("can_share", true)
        put("policy", "anyone"); put("levels", JsonArray(listOf("view", "edit", "share").map(::JsonPrimitive)))
        put("owner", buildJsonObject { put("kind", "user"); put("id", 1); put("name", "Me Example") })
        put("people", JsonArray(shares[id].orEmpty()))
        put("groups", JsonArray(emptyList()))
        put("candidates", JsonArray(listOf(buildJsonObject { put("id", 7); put("name", "Sam Example"); put("email", "sam@example.com") })))
        put("group_candidates", JsonArray(emptyList()))
    }

    private fun conflict(kind: String, id: String, version: Long, theirs: JsonObject) =
        MockResponse().setResponseCode(409).setBody(buildJsonObject {
            put("error", "conflict"); put("conflict", true); put("kind", kind); put("id", id)
            put("detail", "This changed elsewhere"); put("fields", JsonArray(listOf(JsonPrimitive("text"))))
            put("theirs", buildJsonObject { put("version", version); put("content", theirs) })
        }.toString())

    /** Each table's viewer state as the phone set it ("<id>.pinned" → "true"). */
    val tableState = java.util.concurrent.ConcurrentHashMap<String, String>()

    private fun tableRoute(m: String, t: T, rest: List<String>, b: JsonObject?, query: String = ""): MockResponse {
        when {
            rest == listOf("export.csv") && m == "GET" -> return MockResponse().setResponseCode(200).setHeader("Content-Type", "text/csv")
                .setBody(t.columns.joinToString(",") { it["name"]!!.jsonPrimitive.content } + "\n" +
                    rows.values.filter { it.table == t.id && !it.deleted }.joinToString("") { r ->
                        t.columns.joinToString(",") { c -> (r.cells[c["id"]!!.jsonPrimitive.content] as? JsonPrimitive)?.contentOrNull.orEmpty() } + "\n"
                    })
            rest == listOf("duplicate") && m == "POST" -> {
                val copy = table("${t.name} (copy)", t.columns.map { it["name"]!!.jsonPrimitive.content to it["type"]!!.jsonPrimitive.content })
                rows.values.filter { it.table == t.id && !it.deleted }.toList().forEach { r ->
                    val nr = R(java.util.UUID.randomUUID().toString(), copy.id, r.cells, order = r.order)
                    rows[nr.id] = nr
                }
                return ok(buildJsonObject { put("table", tableJson(copy)); put("undo", undo("DELETE", "/tables/${copy.id}")) })
            }
            rest.isEmpty() && m == "GET" -> return ok(tableJson(t))
            rest.isEmpty() && m == "PATCH" -> {
                val lo = b?.get("local_only")?.jsonPrimitive?.booleanOrNull
                if (lo == false && t.localOnly && !t.canLowerLocalOnly) return err(403, "Only the owner can turn Local models only off")
                lo?.let { t.localOnly = it }
                s(b, "name")?.let { t.name = it }; t.version++
                return ok(buildJsonObject { put("table", tableJson(t)); put("undo", undo("PATCH", "/tables/${t.id}")) })
            }
            rest.isEmpty() && m == "DELETE" -> {
                if (query.contains("forever=true")) { tables.remove(t.id); return ok(buildJsonObject { put("ok", true) }) }
                t.deleted = io.github.murtazanasir.mailr.time.AppClock.now(java.time.ZoneOffset.UTC).toOffsetDateTime().toString(); return ok(buildJsonObject { put("ok", true) })
            }
            rest == listOf("restore") -> { t.deleted = null; return ok(buildJsonObject { put("table", tableJson(t)) }) }
            rest == listOf("presence") && m == "POST" -> {
                presence += "table:${t.id}:${s(b, "state")}" + if (b?.get("hello")?.jsonPrimitive?.booleanOrNull == true) ":hello" else ""
                return ok(buildJsonObject { put("ok", true) })
            }
            rest == listOf("state") && m == "PUT" -> {
                b?.get("pinned")?.jsonPrimitive?.booleanOrNull?.let { tableState["${t.id}.pinned"] = it.toString() }
                b?.get("archived")?.jsonPrimitive?.booleanOrNull?.let { tableState["${t.id}.archived"] = it.toString() }
                (b?.get("labels") as? JsonArray)?.let { a -> tableState["${t.id}.labels"] = a.joinToString(",") { it.jsonPrimitive.content } }
                return ok(buildJsonObject { put("state", buildJsonObject {}) })
            }
            rest == listOf("shares", "me") && m == "DELETE" -> {
                tables.remove(t.id)
                return ok(buildJsonObject { put("ok", true); put("undo", undo("POST", "/tables/${t.id}/shares/me")) })
            }
            rest.size == 3 && rest[0] == "rows" && rest[2] == "duplicate" -> {
                val src = rows[rest[1]] ?: return err(404, "No row")
                val r = R(java.util.UUID.randomUUID().toString(), t.id, src.cells, order = src.order + "V")
                rows[r.id] = r
                return ok(buildJsonObject { put("row", rowJson(r)) })
            }
            rest == listOf("rows") && m == "GET" -> return ok(buildJsonObject {
                val live = rows.values.filter { it.table == t.id && !it.deleted }.sortedBy { it.order }
                put("rows", JsonArray(live.map { rowJson(it) })); put("count", live.size); put("total", live.size)
                put("footers", buildJsonObject { put("c1", buildJsonObject { put("agg", "sum"); put("value", live.sumOf { it.cells["c1"]?.jsonPrimitive?.contentOrNull?.toDoubleOrNull() ?: 0.0 }.toString()) }) })
                put("refs", buildJsonObject {})
            })
            rest == listOf("rows") && m == "POST" -> {
                val id = s(b, "id")!!
                if (rows.containsKey(id)) return ok(buildJsonObject { put("rows", JsonArray(listOf(rowJson(rows[id]!!)))) })
                val r = R(id, t.id, b!!["cells"]!!.jsonObject, order = "r${order++}")
                rows[id] = r
                return ok(buildJsonObject { put("rows", JsonArray(listOf(rowJson(r)))); put("undo", undo("POST", "/tables/${t.id}/revert")) })
            }
            rest.size == 2 && rest[0] == "rows" && m == "GET" -> return rows[rest[1]]?.let { ok(rowJson(it)) } ?: err(404, "No row")
            rest.size == 2 && rest[0] == "rows" && m == "PATCH" -> {
                val r = rows[rest[1]] ?: return err(404, "No row")
                conflictOn.remove(r.id)?.let { theirs ->
                    r.cells = JsonObject(r.cells + ("c0" to JsonPrimitive(theirs))); r.version++
                    return conflict("row", r.id, r.version, buildJsonObject { put("cells", r.cells) })
                }
                r.cells = JsonObject((r.cells + b!!["cells"]!!.jsonObject).filterValues { it !is JsonNull })
                r.version++
                return ok(buildJsonObject { put("row", rowJson(r)); put("undo", undo("POST", "/tables/${t.id}/revert")) })
            }
            rest.size == 2 && rest[0] == "rows" && m == "DELETE" -> {
                rows[rest[1]]?.deleted = true
                return ok(buildJsonObject { put("deleted", 1); put("undo", undo("POST", "/tables/${t.id}/rows/restore")) })
            }
            rest == listOf("rows", "bulk") -> {
                b!!["rows"]!!.jsonArray.forEach { rows[it.jsonPrimitive.content]?.deleted = true }
                return ok(buildJsonObject { put("deleted", b["rows"]!!.jsonArray.size) })
            }
            rest.size == 3 && rest[0] == "rows" && rest[2] == "changes" -> return ok(buildJsonObject {
                put("changes", JsonArray(listOf(buildJsonObject { put("seq", 3); put("who", "Dana"); put("at", "2026-10-03T14:02:00"); put("summary", "Amount 80 → 82") })))
                put("values", buildJsonObject {})
            })
            rest.size == 3 && rest[0] == "rows" && rest[2] == "restore" -> {
                val r = rows[rest[1]] ?: return err(404, "No row")
                r.cells = JsonObject(r.cells + ("c1" to JsonPrimitive("80"))); r.version++
                return ok(buildJsonObject { put("row", rowJson(r)); put("undo", undo("POST", "/tables/${t.id}/revert")) })
            }
            rest == listOf("shares") -> return ok(shareDialog(t.id, t.name))
            rest.size == 2 && rest[0] == "shares" && rest[1] != "me" && m == "PUT" -> {
                val list2 = shares.getOrPut(t.id) { mutableListOf() }
                list2.removeAll { s(it, "id") == rest[1] }
                list2 += buildJsonObject { put("kind", "user"); put("id", rest[1].toLong()); put("name", "Sam Example"); put("level", s(b, "level")!!) }
                return ok(shareDialog(t.id, t.name))
            }
            rest.size == 2 && rest[0] == "shares" && rest[1] != "me" && m == "DELETE" -> {
                shares[t.id]?.removeAll { s(it, "id") == rest[1] }
                return ok(shareDialog(t.id, t.name))
            }
        }
        return err(404, "No route")
    }

    fun close() = server.shutdown()
}
