package io.github.murtazanasir.mailr.notesapp.widget

import io.github.murtazanasir.mailr.notesapp.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.notes.data.Checklist
import io.github.murtazanasir.mailr.notes.data.NotesRepository
import io.github.murtazanasir.mailr.notes.data.TopGroup
import io.github.murtazanasir.mailr.notes.ui.Cells
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/** What a widget shows, read from the phone's copy (so it works offline); pure data for the Glance content. */
sealed interface WidgetModel {
    /** No one is signed in, nothing is picked yet, or the item is gone: one line and a tap target. */
    data class Message(val text: String, val pickable: Boolean) : WidgetModel

    data class ChecklistModel(
        val id: String,
        val title: String,
        val color: String?,
        /** Sections in order (the top group has no name), each with its unchecked items in display order. */
        val groups: List<Pair<String?, List<Row>>>,
        val checked: Int,
        /** The list's usual items (the last ones read, so offline too), for the "Usual" chips. */
        val usualItems: List<UsualChip>,
        /** The last check made in the widget, for the 7 s "Undo" chip in the header. */
        val undo: Undo?,
        val readOnly: Boolean,
        val trashed: Boolean,
        /** This list's changes waiting on the phone for the network. */
        val waiting: Int = 0,
    ) : WidgetModel {
        val usual: Int get() = usualItems.size
        data class Row(val id: String, val text: String, val qty: String?)
        data class UsualChip(val key: String, val text: String)
        data class Undo(val itemId: String, val checked: Boolean, val text: String)
    }

    data class NoteModel(val id: String, val title: String, val body: String, val color: String?, val list: Boolean,
                         val trashed: Boolean) : WidgetModel

    data class TableModel(
        val id: String,
        val title: String,
        val color: String?,
        val columns: List<String>,
        val rows: List<Pair<String, List<String>>>,
        val more: Int,
        val footer: String?,
        val canAdd: Boolean,
        val trashed: Boolean,
    ) : WidgetModel
}

object WidgetModels {
    /** Table rows on the widget; the rest count as "n more". */
    const val MAX_ROWS = 8
    /** Lines (items and section names) on the checklist widget; the rest count as "n more". */
    const val MAX_LINES = 30
    const val UNDO_MS = 7_000L
    /** Usual chips on the checklist widget; the rest open the list. */
    const val MAX_USUAL = 3

    suspend fun checklist(repo: NotesRepository, id: String, undo: WidgetModel.ChecklistModel.Undo?, withUsual: Boolean = true): WidgetModel {
        val n = repo.noteNow(id) ?: return WidgetModel.Message(AppText.get(R.string.notesapp_widget_list_gone), true)
        val items = repo.itemsNow(id)
        val sections = repo.sectionsNow(id)
        val layout = Checklist.layout(items, sections, n.top ?: TopGroup(n.topOrderMode), true)
        val groups = layout.groups.filter { it.items.isNotEmpty() }.map { g ->
            g.section?.name to g.items.map { WidgetModel.ChecklistModel.Row(it.id, it.text, it.qty?.chip) }
        }
        // The usual items: read again when there is a network, else the last ones kept on the phone.
        val usual = if (withUsual && n.suggestUsual && n.canEdit) {
            (withTimeoutOrNull(2_000) { repo.usual(id) } ?: repo.cachedUsual(id))?.takeIf { it.enabled }?.items.orEmpty()
        } else emptyList()
        val waiting = repo.ops.first().count { it.parent == id }
        return WidgetModel.ChecklistModel(n.id, n.title.ifBlank { AppText.get(io.github.murtazanasir.mailr.notes.R.string.notes_untitled_list) }, n.color, groups,
            items.count { it.checked }, usual.map { WidgetModel.ChecklistModel.UsualChip(it.key, it.text) }, undo, !n.canEdit,
            n.deletedAt != null, waiting)
    }

    suspend fun note(repo: NotesRepository, id: String): WidgetModel {
        val n = repo.noteNow(id) ?: return WidgetModel.Message(AppText.get(R.string.notesapp_widget_note_gone), true)
        val body = if (n.isList) repo.itemsNow(id).filter { !it.checked }.sortedBy { it.order }.joinToString("\n") { "☐ " + it.text }
        else n.body
        return WidgetModel.NoteModel(n.id, n.title.ifBlank { AppText.get(if (n.isList) io.github.murtazanasir.mailr.notes.R.string.notes_untitled_list else io.github.murtazanasir.mailr.notes.R.string.notes_untitled_note) }, body.take(1_500),
            n.color, n.isList, n.deletedAt != null)
    }

    /**
     * A table view on the widget: the title column and one or two chosen columns,
     * the first rows in the view's order (the default view's last online read; else the phone's row
     * order) and the footer aggregate of that read.
     */
    suspend fun table(repo: NotesRepository, id: String, chosen: List<String>): WidgetModel {
        val t = repo.tableNow(id) ?: return WidgetModel.Message(AppText.get(R.string.notesapp_widget_table_gone), true)
        val primary = Cells.primary(t)
        val view = t.views.firstOrNull { it.default } ?: t.views.firstOrNull()
        val extra = chosen.mapNotNull { c -> t.columns.firstOrNull { it.id == c } }.take(2).ifEmpty { Cells.phoneColumns(t, view) }
        val cols = listOfNotNull(primary) + extra.filter { it.id != primary?.id }
        val page = repo.viewPage(id, view?.id).first()
        val local = repo.rows(id).first().map { it.row }
        val ordered = page?.rows?.mapNotNull { r -> local.firstOrNull { it.id == r.id } ?: r }?.ifEmpty { null } ?: local.sortedBy { it.order }
        val refs = page?.refs.orEmpty()
        val rows = ordered.take(MAX_ROWS).map { r -> r.id to cols.map { c -> Cells.text(c, r.cells[c.id], refs) } }
        val footer = page?.footers?.entries?.firstOrNull { it.value.value != null && it.value.value !is JsonNull }?.let { (col, f) ->
            val name = t.columns.firstOrNull { it.id == col }?.name ?: col
            val v = when (val x = f.value) {
                is JsonPrimitive -> x.contentOrNull ?: ""
                is JsonObject -> (x["top"] as? JsonArray)?.joinToString(", ") { e ->
                    "${e.jsonObject["name"]?.jsonPrimitive?.contentOrNull} ${e.jsonObject["count"]?.jsonPrimitive?.contentOrNull}"
                } ?: x.toString()
                else -> x.toString()
            }
            AppText.get(io.github.murtazanasir.mailr.notes.R.string.notes_footer_stat, name, f.agg.orEmpty(), v).replace("  ", " ")
        } ?: AppText.plural(io.github.murtazanasir.mailr.notes.R.plurals.notes_rows_count, ordered.size, ordered.size)
        return WidgetModel.TableModel(t.id, t.name.ifBlank { AppText.get(io.github.murtazanasir.mailr.notes.R.string.notes_untitled_table) }, t.color, cols.map { it.name }, rows,
            (ordered.size - MAX_ROWS).coerceAtLeast(0), footer, t.canEdit, t.deletedAt != null)
    }
}
