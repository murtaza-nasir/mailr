package io.github.murtazanasir.mailr.notes.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/**
 * The shapes of the notes and tables REST APIs. Fields the screens do not read stay in
 * the cached JSON and round-trip untouched.
 */
val NotesJson = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    coerceInputValues = true
    encodeDefaults = true
    isLenient = true
}

@Serializable
data class Person(val kind: String? = null, val id: Long? = null, val name: String? = null)

@Serializable
data class Owner(val kind: String = "user", val id: Long? = null, val name: String? = null)

@Serializable
data class ViewerState(
    val pinned: Boolean = false,
    val archived: Boolean = false,
    val labels: List<String> = emptyList(),
    val order: String? = null,
    @SerialName("seen_version") val seenVersion: Long? = null,
)

@Serializable
data class Sharing(val count: Int = 0, val people: List<Person> = emptyList())

@Serializable
data class Counts(val items: Int = 0, val unchecked: Int = 0, val checked: Int = 0, val sections: Int = 0,
                  val rows: Int = 0, val columns: Int = 0)

@Serializable
data class NoteLink(
    val id: Long,
    @SerialName("target_kind") val targetKind: String,
    @SerialName("target_ref") val targetRef: JsonObject = JsonObject(emptyMap()),
    val title: String? = null,
)

@Serializable
data class Qty(val amount: String, val unit: String? = null, val text: String? = null) {
    /** The chip: "2", "500 g"; a count of 1 shows no chip. */
    val chip: String? get() = when {
        unit == null && amount == "1" -> null
        unit == null -> amount
        else -> "$amount $unit"
    }
}

@Serializable
data class TopGroup(@SerialName("order_mode") val orderMode: String = "manual", val collapsed: Boolean = false,
                    val trips: Int = 0, val unchecked: Int = 0)

@Serializable
data class Note(
    val id: String,
    val kind: String = "text",
    val title: String = "",
    val body: String = "",
    val color: String? = null,
    val shopping: Boolean = false,
    @SerialName("checked_at_bottom") val checkedAtBottom: Boolean = true,
    @SerialName("suggest_usual") val suggestUsual: Boolean = false,
    @SerialName("local_only") val localOnly: Boolean = false,
    /** Whether this viewer may turn [localOnly] off: the owner only (for a group's item, its creator). */
    @SerialName("can_lower_local_only") val canLowerLocalOnly: Boolean = true,
    /** A repeating list: its rule and the next and last reset, or null. */
    val repeat: NoteRepeat? = null,
    @SerialName("top_order_mode") val topOrderMode: String = "manual",
    val owner: Owner? = null,
    @SerialName("created_by") val createdBy: Person? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
    @SerialName("updated_by") val updatedBy: Person? = null,
    @SerialName("deleted_at") val deletedAt: String? = null,
    @SerialName("purge_at") val purgeAt: String? = null,
    val version: Long? = null,
    val level: String = "owner",
    @SerialName("can_delete") val canDelete: Boolean = true,
    val sharing: Sharing = Sharing(),
    val changed: Boolean = false,
    val state: ViewerState = ViewerState(),
    val counts: Counts = Counts(),
    val links: List<NoteLink> = emptyList(),
    /** This viewer's reminders, soonest first; handled ones are left out. */
    val reminders: List<NoteReminder> = emptyList(),
    @SerialName("next_reminder") val nextReminder: String? = null,
    val top: TopGroup? = null,
    val sections: List<Section>? = null,
    val items: List<Item>? = null,
) {
    val isList: Boolean get() = kind == "checklist"
    val canEdit: Boolean get() = level == "edit" || level == "share" || level == "owner"
    val canShare: Boolean get() = level == "share" || level == "owner"
    val shared: Boolean get() = sharing.count > 0 || owner?.kind == "group" || level != "owner"
}

/** A list's repeat: `FREQ=WEEKLY;BYDAY=SU` or `FREQ=DAILY;INTERVAL=3`, and the next and last reset (ISO times). */
@Serializable
data class NoteRepeat(val rule: String, val next: String? = null, val last: String? = null)

/** A reminder of the viewer's on a note: key `n|<note id>|abs|<instant>`. */
@Serializable
data class NoteReminder(val key: String, val at: String, val state: String = "upcoming",
                        @SerialName("snoozed_until") val snoozedUntil: String? = null)

/** A template: a built-in starter or the user's own. */
@Serializable
data class Template(val id: String, val kind: String = "text", val name: String = "", val builtin: Boolean = false,
                    val summary: String? = null)

@Serializable
data class Section(
    val id: String,
    val name: String,
    val order: String = "",
    @SerialName("order_mode") val orderMode: String = "manual",
    val collapsed: Boolean = false,
    val trips: Int = 0,
    val unchecked: Int = 0,
)

@Serializable
data class Item(
    val id: String,
    @SerialName("section_id") val sectionId: String? = null,
    val text: String = "",
    val key: String = "",
    val qty: Qty? = null,
    val extra: String? = null,
    val checked: Boolean = false,
    @SerialName("checked_at") val checkedAt: String? = null,
    @SerialName("checked_by") val checkedBy: Person? = null,
    val order: String = "",
    val rank: Double? = null,
    @SerialName("added_by") val addedBy: Person? = null,
    @SerialName("added_at") val addedAt: String? = null,
    @SerialName("updated_by") val updatedBy: Person? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
    val version: Long? = null,
    @SerialName("task_uid") val taskUid: String? = null,
)

@Serializable
data class Label(val name: String, val color: String? = null, val order: String? = null, val count: Int = 0)

@Serializable
data class Undo(val method: String, val path: String, val body: JsonElement? = null, val label: String = "")

@Serializable
data class Revision(val rev: Long, val at: String? = null, val by: Person? = null, val source: String = "edit",
                    val summary: String? = null, val content: JsonObject? = null)

@Serializable
data class UsualItem(
    val key: String,
    val text: String,
    val qty: Qty? = null,
    @SerialName("section_id") val sectionId: String? = null,
    val section: String? = null,
    @SerialName("days_since") val daysSince: Double? = null,
    val interval: Double? = null,
    val count: Int = 0,
)

@Serializable
data class Usual(val items: List<UsualItem> = emptyList(), val enabled: Boolean = true)

// ---------------------------------------------------------------- tables

@Serializable
data class SelectOption(val id: String, val name: String, val color: String? = null)

@Serializable
data class Column(
    val id: String,
    val name: String,
    val type: String = "text",
    val config: JsonObject = JsonObject(emptyMap()),
    val required: Boolean = false,
    val order: String = "",
)

@Serializable
data class ViewSpec(
    val sort: List<JsonObject> = emptyList(),
    val filters: List<JsonObject> = emptyList(),
    val hidden: List<String> = emptyList(),
    val footers: Map<String, String> = emptyMap(),
    @SerialName("phone_columns") val phoneColumns: List<String> = emptyList(),
)

@Serializable
data class TableView(val id: String, val name: String, val default: Boolean = false, val order: String = "",
                     val spec: ViewSpec = ViewSpec())

@Serializable
data class TablePreview(val columns: List<JsonObject> = emptyList(), val rows: List<List<JsonElement?>> = emptyList(),
                        val more: Int = 0)

@Serializable
data class DataTable(
    val id: String,
    val name: String = "",
    val description: String = "",
    val color: String? = null,
    @SerialName("local_only") val localOnly: Boolean = false,
    @SerialName("can_lower_local_only") val canLowerLocalOnly: Boolean = true,
    @SerialName("primary_column") val primaryColumn: String? = null,
    val owner: Owner? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
    @SerialName("updated_by") val updatedBy: Person? = null,
    @SerialName("deleted_at") val deletedAt: String? = null,
    @SerialName("purge_at") val purgeAt: String? = null,
    val version: Long? = null,
    val level: String = "owner",
    @SerialName("can_delete") val canDelete: Boolean = true,
    val sharing: Sharing = Sharing(),
    val changed: Boolean = false,
    val state: ViewerState = ViewerState(),
    val counts: Counts = Counts(),
    val links: List<NoteLink> = emptyList(),
    val columns: List<Column> = emptyList(),
    val views: List<TableView> = emptyList(),
    val preview: TablePreview? = null,
) {
    val canEdit: Boolean get() = level == "edit" || level == "share" || level == "owner"
    val canShare: Boolean get() = level == "share" || level == "owner"
    val shared: Boolean get() = sharing.count > 0 || owner?.kind == "group" || level != "owner"
}

@Serializable
data class Row(
    val id: String,
    val cells: JsonObject = JsonObject(emptyMap()),
    val raw: JsonObject = JsonObject(emptyMap()),
    val order: String = "",
    @SerialName("created_by") val createdBy: Person? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_by") val updatedBy: Person? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
    val version: Long? = null,
    @SerialName("deleted_at") val deletedAt: String? = null,
    val table: String? = null,
)

@Serializable
data class RowChange(
    val seq: Long,
    val at: String? = null,
    val who: String? = null,
    val op: String = "edit",
    val summary: String? = null,
)

@Serializable
data class CellValue(val value: JsonElement? = null, val text: String? = null, val seq: Long = 0, val at: String? = null,
                     val who: String? = null)

@Serializable
data class RowHistory(val changes: List<RowChange> = emptyList(), val values: Map<String, List<CellValue>> = emptyMap(),
                      val deleted: Boolean = false)

@Serializable
data class Footer(val agg: String? = null, val value: JsonElement? = null)

@Serializable
data class RowsPage(
    val rows: List<Row> = emptyList(),
    val count: Int = 0,
    val total: Int = 0,
    val footers: Map<String, Footer> = emptyMap(),
    val refs: Map<String, JsonObject> = emptyMap(),
)

// ---------------------------------------------------------------- sync

@Serializable
data class NotesDelta(val notes: List<Note> = emptyList(), val trashed: List<String> = emptyList(),
                      val visible: Map<String, String>? = null, val now: String)

@Serializable
data class DeletedRow(val table: String, val id: String)

@Serializable
data class TablesDelta(
    val tables: List<DataTable> = emptyList(),
    val rows: List<Row> = emptyList(),
    @SerialName("deleted_rows") val deletedRows: List<DeletedRow> = emptyList(),
    val trashed: List<String> = emptyList(),
    val visible: Map<String, String>? = null,
    val now: String,
)

// ---------------------------------------------------------------- sharing

@Serializable
data class SharePerson(
    val kind: String = "user",
    val id: Long? = null,
    val name: String = "",
    val email: String? = null,
    val level: String = "view",
    val disabled: Boolean = false,
    val you: Boolean = false,
    @SerialName("outside_policy") val outsidePolicy: Boolean = false,
    val members: Int? = null,
)

@Serializable
data class Candidate(val id: Long, val name: String = "", val email: String? = null)

@Serializable
data class ShareDialog(
    val id: String,
    val kind: String = "note",
    val noun: String = "note",
    val name: String = "",
    val level: String = "owner",
    val own: Boolean = true,
    @SerialName("can_share") val canShare: Boolean = false,
    val policy: String = "anyone",
    val levels: List<String> = listOf("view", "edit", "share"),
    val owner: Owner? = null,
    val people: List<SharePerson> = emptyList(),
    val groups: List<SharePerson> = emptyList(),
    @SerialName("can_leave") val canLeave: Boolean = false,
    val candidates: List<Candidate> = emptyList(),
    @SerialName("group_candidates") val groupCandidates: List<String> = emptyList(),
)

/** The note colours, in menu order. */
val NOTE_COLORS = listOf("tomato", "flamingo", "tangerine", "banana", "sage", "basil", "peacock", "blueberry",
    "lavender", "grape", "graphite", "cocoa")
