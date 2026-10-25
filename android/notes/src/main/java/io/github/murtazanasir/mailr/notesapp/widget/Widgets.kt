package io.github.murtazanasir.mailr.notesapp.widget

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.i18n.AppText
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.ColorFilter
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.GlanceTheme
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetManager
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.provideContent
import androidx.glance.appwidget.updateAll
import androidx.glance.background
import androidx.glance.color.ColorProvider
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.semantics.contentDescription
import androidx.glance.semantics.semantics
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import io.github.murtazanasir.mailr.notes.ui.NoteColors
import io.github.murtazanasir.mailr.notesapp.DeepLinks
import io.github.murtazanasir.mailr.notesapp.MainActivity
import io.github.murtazanasir.mailr.notesapp.NotesApplication
import io.github.murtazanasir.mailr.notesapp.NotesGraph
import io.github.murtazanasir.mailr.notesapp.R
import io.github.murtazanasir.mailr.notesapp.capture.CaptureActivity
import io.github.murtazanasir.mailr.notesapp.data.WidgetTarget
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * The Notes app's home-screen widgets (surface `android.noteswidget`): a
 * checklist with checks in place, a note, a table view, and capture buttons. Each is picked when
 * placed ([WidgetPickActivity]) and re-pickable later. They read the phone's copy, so they work
 * offline; a check goes through the replay queue like a check in the app. They are redrawn after
 * every change of the copy (a write here, a sync, a live event; NotesApplication) and by the
 * periodic sync.
 */
object Widgets {
    suspend fun updateAll(context: Context) {
        ChecklistWidget().updateAll(context)
        NoteWidget().updateAll(context)
        TableWidget().updateAll(context)
        CaptureWidget().updateAll(context)
    }

    fun graph(context: Context): NotesGraph? = (context.applicationContext as? NotesApplication)?.graph

    /** The note, list or table a widget shows, by its Glance id. */
    suspend fun target(context: Context, id: GlanceId): Pair<Int, WidgetTarget?> {
        val appWidgetId = GlanceAppWidgetManager(context).getAppWidgetId(id)
        return appWidgetId to graph(context)?.prefs?.widget(appWidgetId)
    }

    fun open(context: Context, uri: String): Intent =
        Intent(Intent.ACTION_VIEW, Uri.parse(uri), context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)

    fun pick(context: Context, appWidgetId: Int): Intent =
        Intent(context, WidgetPickActivity::class.java).putExtra(android.appwidget.AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId)
            .setData(Uri.parse("mailr-notes-widget://pick/$appWidgetId"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

    /** The widget's background: the note's colour (light and dark), else the theme's surface. */
    @Composable
    fun background(color: String?): androidx.glance.unit.ColorProvider {
        val day = NoteColors.argb(color, dark = false)
        val night = NoteColors.argb(color, dark = true)
        return if (day != null && night != null) ColorProvider(Color(day), Color(night)) else GlanceTheme.colors.widgetBackground
    }

    @Composable
    fun content(color: String?): androidx.glance.unit.ColorProvider =
        if (color == null) GlanceTheme.colors.onSurface else ColorProvider(Color(0xFF1F1F1F), Color(0xFFF1F1F1))
}

/** Last checks made in widgets, for the header's 7 s "Undo" chip (one per list). */
object WidgetUndo {
    private val last = java.util.concurrent.ConcurrentHashMap<String, Pair<WidgetModel.ChecklistModel.Undo, Long>>()

    fun put(listId: String, u: WidgetModel.ChecklistModel.Undo, now: Long = AppClock.millis()) { last[listId] = u to now }
    fun get(listId: String, now: Long = AppClock.millis()): WidgetModel.ChecklistModel.Undo? =
        last[listId]?.takeIf { now - it.second < WidgetModels.UNDO_MS }?.first
    fun clear(listId: String) { last.remove(listId) }
}

/** What the widgets do: here once, so the callbacks stay thin and the tests call these. */
object WidgetActions {
    /**
     * ACT checkitem.check @android.noteswidget, ACT checkitem.uncheck @android.noteswidget: one tap
     * on a box, through the replay queue (offline too), with the device time of the tap. The header
     * offers "Undo" for 7 s.
     */
    suspend fun check(g: NotesGraph, listId: String, itemId: String, checked: Boolean) {
        val text = g.notes.repo.itemsNow(listId).firstOrNull { it.id == itemId }?.text.orEmpty()
        g.notes.repo.check(itemId, checked)
        WidgetUndo.put(listId, WidgetModel.ChecklistModel.Undo(itemId, checked, text))
    }

    /**
     * ACT checkitem.usual_add @android.noteswidget: a "Usual" chip adds that item to the list through
     * the queue (offline too), from the suggestions last read for it.
     */
    suspend fun addUsual(g: NotesGraph, listId: String, key: String): Boolean {
        val repo = g.notes.repo
        val u = repo.cachedUsual(listId)?.items?.firstOrNull { it.key == key } ?: return false
        repo.addUsual(listId, u)
        return true
    }

    /** The header's "Undo": the check is reversed (another check through the queue). */
    suspend fun undo(g: NotesGraph, listId: String) {
        val u = WidgetUndo.get(listId) ?: return
        WidgetUndo.clear(listId)
        g.notes.repo.check(u.itemId, !u.checked)
    }
}

val KEY_LIST = ActionParameters.Key<String>("list")
val KEY_ITEM = ActionParameters.Key<String>("item")
val KEY_CHECKED = ActionParameters.Key<Boolean>("checked")
val KEY_USUAL = ActionParameters.Key<String>("usual")

class AddUsualAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val g = Widgets.graph(context) ?: return
        WidgetActions.addUsual(g, parameters[KEY_LIST] ?: return, parameters[KEY_USUAL] ?: return)
        ChecklistWidget().update(context, glanceId)
    }
}

class CheckItemAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val g = Widgets.graph(context) ?: return
        val list = parameters[KEY_LIST] ?: return
        val item = parameters[KEY_ITEM] ?: return
        WidgetActions.check(g, list, item, parameters[KEY_CHECKED] ?: true)
        ChecklistWidget().update(context, glanceId)
        // The Undo chip goes when its 7 s are up.
        g.scope.launch { delay(WidgetModels.UNDO_MS + 300); catching { ChecklistWidget().update(context, glanceId) } }
    }
}

class UndoCheckAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val g = Widgets.graph(context) ?: return
        WidgetActions.undo(g, parameters[KEY_LIST] ?: return)
        ChecklistWidget().update(context, glanceId)
    }
}

// ================================================================ checklist

class ChecklistWidget : GlanceAppWidget() {
    // The widget's real size, so the list shows as many items as fit.
    override val sizeMode: androidx.glance.appwidget.SizeMode = androidx.glance.appwidget.SizeMode.Exact

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val (appWidgetId, target) = Widgets.target(context, id)
        val g = Widgets.graph(context)
        val model = when {
            g == null || g.sessions.session.value == null -> WidgetModel.Message(AppText.get(R.string.notesapp_widget_sign_in_list), false)
            target == null -> WidgetModel.Message(AppText.get(R.string.notesapp_widget_choose_list), true)
            else -> WidgetModels.checklist(g.notes.repo, target.id, WidgetUndo.get(target.id))
        }
        provideContent { GlanceTheme { ChecklistContent(model, appWidgetId) } }
    }
}

@Composable
fun MessageContent(m: WidgetModel.Message, appWidgetId: Int) {
    val ctx = androidx.glance.LocalContext.current
    Box(GlanceModifier.fillMaxSize().background(GlanceTheme.colors.widgetBackground).cornerRadius(16.dp).padding(16.dp)
        .clickable(actionStartActivity(if (m.pickable) Widgets.pick(ctx, appWidgetId) else Intent(ctx, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))),
        contentAlignment = Alignment.Center) {
        Text(m.text, style = TextStyle(color = GlanceTheme.colors.onSurface, fontSize = 14.sp))
    }
}

@Composable
fun ChecklistContent(model: WidgetModel, appWidgetId: Int) {
    if (model is WidgetModel.Message) return MessageContent(model, appWidgetId)
    val m = model as WidgetModel.ChecklistModel
    val ctx = androidx.glance.LocalContext.current
    val fg = Widgets.content(m.color)
    Column(GlanceModifier.fillMaxSize().background(Widgets.background(m.color)).cornerRadius(16.dp).padding(12.dp)) {
        Row(GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(m.title, maxLines = 1, style = TextStyle(color = fg, fontWeight = FontWeight.Bold, fontSize = 16.sp),
                modifier = GlanceModifier.defaultWeight().clickable(actionStartActivity(Widgets.open(ctx, DeepLinks.noteUri(m.id)))))
            m.undo?.let { u ->
                Text(AppText.get(io.github.murtazanasir.mailr.notes.R.string.notes_op_undo), style = TextStyle(color = GlanceTheme.colors.onPrimaryContainer, fontWeight = FontWeight.Medium, fontSize = 13.sp),
                    modifier = GlanceModifier.background(GlanceTheme.colors.primaryContainer).cornerRadius(12.dp)
                        .padding(horizontal = 10.dp, vertical = 4.dp)
                        .semantics { contentDescription = AppText.get(if (u.checked) R.string.notesapp_widget_undo_check else R.string.notesapp_widget_undo_uncheck, u.text) }
                        .clickable(actionRunCallback<UndoCheckAction>(actionParametersOf(KEY_LIST to m.id))))
                Spacer(GlanceModifier.width(8.dp))
            }
            // ACT checkitem.voice_add @android.noteswidget: the microphone adds spoken items to this list (on-device recognition).
            if (!m.readOnly && !m.trashed) Image(ImageProvider(R.drawable.ic_mic), AppText.get(R.string.notesapp_widget_dictate_for, m.title),
                colorFilter = ColorFilter.tint(fg),
                modifier = GlanceModifier.size(32.dp).padding(4.dp)
                    .clickable(actionStartActivity(CaptureActivity.intent(ctx, "voice", m.id))))
            if (!m.readOnly && !m.trashed) Image(ImageProvider(R.drawable.ic_add), AppText.get(R.string.notesapp_add_to_list_named, m.title),
                colorFilter = ColorFilter.tint(fg),
                // ACT checkitem.add @android.noteswidget, ACT note.add_items @android.noteswidget: "+" opens a one-line add sheet bound to this list.
                modifier = GlanceModifier.size(32.dp).padding(4.dp)
                    .clickable(actionStartActivity(CaptureActivity.intent(ctx, "add", m.id))))
        }
        if (m.trashed) {
            Text(AppText.get(R.string.notesapp_list_in_trash), style = TextStyle(color = fg, fontSize = 13.sp), modifier = GlanceModifier.padding(top = 8.dp))
            return@Column
        }
        Spacer(GlanceModifier.height(4.dp))
        val rows = buildList<Any> {
            m.groups.forEach { (name, items) ->
                if (name != null) add(name)
                addAll(items)
            }
        }
        // A plain column of the first rows (no scrolling collection): it draws the same on every
        // launcher; "n more" opens the list.
        // As many lines as the widget's height holds (48 dp an item, 26 dp a section name).
        val chips = !m.readOnly && m.usualItems.isNotEmpty()
        val room = androidx.glance.LocalSize.current.height.value - 96f - (if (chips) 40f else 0f)
        var used = 0f
        val shown = rows.takeWhile { r -> used += if (r is String) 26f else 48f; used <= room }.take(WidgetModels.MAX_LINES)
        Column(GlanceModifier.defaultWeight().fillMaxWidth()) {
            shown.forEach { r ->
                when (r) {
                    is String -> Text(r, style = TextStyle(color = fg, fontWeight = FontWeight.Medium, fontSize = 12.sp),
                        modifier = GlanceModifier.padding(top = 6.dp, bottom = 2.dp))
                    is WidgetModel.ChecklistModel.Row -> ItemRow(m, r, fg)
                }
            }
            val more = rows.drop(shown.size).count { it is WidgetModel.ChecklistModel.Row }
            if (more > 0) Text(AppText.get(R.string.notesapp_more_count, more), style = TextStyle(color = fg, fontSize = 13.sp),
                modifier = GlanceModifier.padding(top = 4.dp).clickable(actionStartActivity(Widgets.open(ctx, DeepLinks.noteUri(m.id)))))
        }
        if (chips) UsualChips(m, fg)
        val foot = buildList<String> {
            if (m.checked > 0) add(AppText.get(io.github.murtazanasir.mailr.notes.R.string.notes_count_checked, m.checked))
            // Offline: what waits on the phone for the network.
            if (m.waiting > 0) add(AppText.plural(R.plurals.notesapp_changes_waiting, m.waiting, m.waiting))
        }
        if (foot.isNotEmpty()) Text(foot.joinToString(" · "), style = TextStyle(color = fg, fontSize = 12.sp),
            modifier = GlanceModifier.padding(top = 4.dp).clickable(actionStartActivity(Widgets.open(ctx, DeepLinks.noteUri(m.id)))))
        if (m.groups.isEmpty()) Text(AppText.get(R.string.notesapp_widget_nothing_left), style = TextStyle(color = fg, fontSize = 13.sp))
    }
}

/** "Usual: + coffee  + eggs": a tap adds one (hidden when the list has none); the last chip opens the list for the rest. */
@Composable
private fun UsualChips(m: WidgetModel.ChecklistModel, fg: androidx.glance.unit.ColorProvider) {
    val ctx = androidx.glance.LocalContext.current
    val shown = m.usualItems.take(WidgetModels.MAX_USUAL)
    Row(GlanceModifier.fillMaxWidth().padding(top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(AppText.get(R.string.notesapp_widget_usual), style = TextStyle(color = fg, fontSize = 12.sp, fontWeight = FontWeight.Medium))
        shown.forEach { u ->
            Spacer(GlanceModifier.width(6.dp))
            Text("+ ${u.text}", maxLines = 1, style = TextStyle(color = GlanceTheme.colors.onSecondaryContainer, fontSize = 13.sp),
                modifier = GlanceModifier.background(GlanceTheme.colors.secondaryContainer).cornerRadius(12.dp)
                    .padding(horizontal = 10.dp, vertical = 6.dp)
                    .semantics { contentDescription = AppText.get(R.string.notesapp_widget_add_usual, u.text, m.title) }
                    .clickable(actionRunCallback<AddUsualAction>(actionParametersOf(KEY_LIST to m.id, KEY_USUAL to u.key))))
        }
        val more = m.usualItems.size - shown.size
        if (more > 0) {
            Spacer(GlanceModifier.width(6.dp))
            Text(AppText.get(R.string.notesapp_more_count, more), style = TextStyle(color = fg, fontSize = 12.sp),
                modifier = GlanceModifier.padding(4.dp).clickable(actionStartActivity(Widgets.open(ctx, DeepLinks.noteUri(m.id)))))
        }
    }
}

@Composable
private fun ItemRow(m: WidgetModel.ChecklistModel, r: WidgetModel.ChecklistModel.Row, fg: androidx.glance.unit.ColorProvider) {
    val label = (r.qty?.let { "$it " } ?: "") + r.text
    val check = actionRunCallback<CheckItemAction>(actionParametersOf(KEY_LIST to m.id, KEY_ITEM to r.id, KEY_CHECKED to true))
    // The whole row is the tap target (at least 48 dp tall); TalkBack reads "Check milk" as a button.
    Row(GlanceModifier.fillMaxWidth().height(48.dp).let { if (m.readOnly) it else it.clickable(check) }
        .semantics { contentDescription = if (m.readOnly) label else AppText.get(io.github.murtazanasir.mailr.notes.R.string.notes_check_item, label) },
        verticalAlignment = Alignment.CenterVertically) {
        Image(ImageProvider(R.drawable.ic_check_box_outline), null, colorFilter = ColorFilter.tint(fg), modifier = GlanceModifier.size(22.dp))
        Spacer(GlanceModifier.width(10.dp))
        Text(label, maxLines = 2, style = TextStyle(color = fg, fontSize = 14.sp), modifier = GlanceModifier.defaultWeight())
    }
}

class ChecklistWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = ChecklistWidget()
}

// ================================================================ note

class NoteWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val (appWidgetId, target) = Widgets.target(context, id)
        val g = Widgets.graph(context)
        val model = when {
            g == null || g.sessions.session.value == null -> WidgetModel.Message(AppText.get(R.string.notesapp_widget_sign_in_note), false)
            target == null -> WidgetModel.Message(AppText.get(R.string.notesapp_widget_choose_note), true)
            else -> WidgetModels.note(g.notes.repo, target.id)
        }
        provideContent { GlanceTheme { NoteContent(model, appWidgetId) } }
    }
}

@Composable
fun NoteContent(model: WidgetModel, appWidgetId: Int) {
    if (model is WidgetModel.Message) return MessageContent(model, appWidgetId)
    val m = model as WidgetModel.NoteModel
    val ctx = androidx.glance.LocalContext.current
    val fg = Widgets.content(m.color)
    Column(GlanceModifier.fillMaxSize().background(Widgets.background(m.color)).cornerRadius(16.dp).padding(12.dp)
        // A tap opens the editor.
        .clickable(actionStartActivity(Widgets.open(ctx, DeepLinks.noteUri(m.id))))) {
        Text(m.title, maxLines = 2, style = TextStyle(color = fg, fontWeight = FontWeight.Bold, fontSize = 16.sp))
        Spacer(GlanceModifier.height(4.dp))
        Text(if (m.trashed) AppText.get(R.string.notesapp_note_in_trash) else m.body, maxLines = 12, style = TextStyle(color = fg, fontSize = 14.sp))
    }
}

class NoteWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = NoteWidget()
}

// ================================================================ table

class TableWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val (appWidgetId, target) = Widgets.target(context, id)
        val g = Widgets.graph(context)
        val model = when {
            g == null || g.sessions.session.value == null -> WidgetModel.Message(AppText.get(R.string.notesapp_widget_sign_in_table), false)
            target == null -> WidgetModel.Message(AppText.get(R.string.notesapp_widget_choose_table), true)
            else -> WidgetModels.table(g.notes.repo, target.id, target.columns)
        }
        provideContent { GlanceTheme { TableContent(model, appWidgetId) } }
    }
}

/** ACT table.stats @android.noteswidget: the first rows and the footer aggregate. */
@Composable
fun TableContent(model: WidgetModel, appWidgetId: Int) {
    if (model is WidgetModel.Message) return MessageContent(model, appWidgetId)
    val m = model as WidgetModel.TableModel
    val ctx = androidx.glance.LocalContext.current
    val fg = Widgets.content(m.color)
    Column(GlanceModifier.fillMaxSize().background(Widgets.background(m.color)).cornerRadius(16.dp).padding(12.dp)) {
        Row(GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(m.title, maxLines = 1, style = TextStyle(color = fg, fontWeight = FontWeight.Bold, fontSize = 16.sp),
                modifier = GlanceModifier.defaultWeight().clickable(actionStartActivity(Widgets.open(ctx, DeepLinks.tableUri(m.id)))))
            // ACT row.add @android.noteswidget: "+" opens the row form.
            if (m.canAdd && !m.trashed) Image(ImageProvider(R.drawable.ic_add), AppText.get(R.string.notesapp_widget_add_row_to, m.title), colorFilter = ColorFilter.tint(fg),
                modifier = GlanceModifier.size(32.dp).padding(4.dp)
                    .clickable(actionStartActivity(Widgets.open(ctx, DeepLinks.tableUri(m.id, "new")))))
        }
        if (m.trashed) {
            Text(AppText.get(R.string.notesapp_table_in_trash), style = TextStyle(color = fg, fontSize = 13.sp))
            return@Column
        }
        Row(GlanceModifier.fillMaxWidth().padding(top = 4.dp)) {
            m.columns.forEach { c -> Text(c, maxLines = 1, style = TextStyle(color = fg, fontWeight = FontWeight.Medium, fontSize = 12.sp),
                modifier = GlanceModifier.defaultWeight()) }
        }
        Column(GlanceModifier.defaultWeight().fillMaxWidth()) {
            m.rows.forEach { (rowId, cells) ->
                Row(GlanceModifier.fillMaxWidth().padding(vertical = 3.dp)
                    .semantics { contentDescription = m.columns.zip(cells).joinToString(", ") { (c, v) -> "$c $v" } }
                    // A tap on a row opens it.
                    .clickable(actionStartActivity(Widgets.open(ctx, DeepLinks.tableUri(m.id, rowId))))) {
                    cells.forEachIndexed { j, v -> Text(v.ifBlank { "–" }, maxLines = 1,
                        style = TextStyle(color = fg, fontSize = 13.sp, fontWeight = if (j == 0) FontWeight.Medium else FontWeight.Normal),
                        modifier = GlanceModifier.defaultWeight()) }
                }
            }
        }
        Text(listOfNotNull(m.footer, if (m.more > 0) AppText.get(R.string.notesapp_more_count, m.more) else null).joinToString(" · "),
            style = TextStyle(color = fg, fontSize = 12.sp), modifier = GlanceModifier.padding(top = 4.dp))
    }
}

class TableWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = TableWidget()
}

// ================================================================ capture

class CaptureWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val g = Widgets.graph(context)
        val list = g?.prefs?.defaultList?.let { g.notes.repo.noteNow(it) }?.takeIf { it.deletedAt == null }
        val signedIn = g?.sessions?.session?.value != null
        provideContent { GlanceTheme { CaptureContent(signedIn, list?.id, list?.title?.ifBlank { AppText.get(io.github.murtazanasir.mailr.notes.R.string.notes_untitled_list) }) } }
    }
}

/**
 * The capture widget: New note, New list, Add to <default list>, Dictate; each opens quick capture.
 * ACT note.create @android.noteswidget
 */
@Composable
fun CaptureContent(signedIn: Boolean, listId: String?, listTitle: String?) {
    val ctx = androidx.glance.LocalContext.current
    Row(GlanceModifier.fillMaxSize().background(GlanceTheme.colors.widgetBackground).cornerRadius(24.dp).padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically) {
        if (!signedIn) {
            Text(AppText.get(R.string.notesapp_sign_in_to_notes), style = TextStyle(color = GlanceTheme.colors.onSurface, fontSize = 14.sp),
                modifier = GlanceModifier.defaultWeight().clickable(actionStartActivity(Intent(ctx, MainActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))))
            return@Row
        }
        @Composable
        fun button(icon: Int, label: String, intent: Intent) {
            Row(GlanceModifier.defaultWeight().padding(6.dp).clickable(actionStartActivity(intent)).semantics { contentDescription = label },
                verticalAlignment = Alignment.CenterVertically, horizontalAlignment = Alignment.CenterHorizontally) {
                Image(ImageProvider(icon), null, colorFilter = ColorFilter.tint(GlanceTheme.colors.onSurface), modifier = GlanceModifier.size(22.dp))
            }
        }
        button(io.github.murtazanasir.mailr.notes.R.drawable.ic_note, AppText.get(io.github.murtazanasir.mailr.notes.R.string.notes_new_note), CaptureActivity.intent(ctx, "note"))
        button(io.github.murtazanasir.mailr.notes.R.drawable.ic_list, AppText.get(io.github.murtazanasir.mailr.notes.R.string.notes_new_list), CaptureActivity.intent(ctx, "list"))
        button(R.drawable.ic_add, if (listTitle != null) AppText.get(R.string.notesapp_add_to_list_named, listTitle) else AppText.get(R.string.notesapp_add_to_a_list), CaptureActivity.intent(ctx, "add", listId))
        // ACT checkitem.voice_add @android.noteswidget: "add milk to Shopping" adds to that list; other words become a note.
        button(R.drawable.ic_mic, AppText.get(R.string.notesapp_dictate), CaptureActivity.intent(ctx, "voice"))
    }
}

class CaptureWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = CaptureWidget()
}
