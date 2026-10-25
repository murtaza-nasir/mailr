package io.github.murtazanasir.mailr.notesapp.widget

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.notesapp.ui.followHideContent

import androidx.annotation.StringRes
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.notesapp.R
import io.github.murtazanasir.mailr.i18n.AppText
import android.app.Activity
import android.appwidget.AppWidgetManager
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import io.github.murtazanasir.mailr.look.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.murtazanasir.mailr.notes.ui.Cells
import io.github.murtazanasir.mailr.notesapp.NotesGraph
import io.github.murtazanasir.mailr.notesapp.NotesApplication
import io.github.murtazanasir.mailr.notesapp.capture.Choices
import io.github.murtazanasir.mailr.notesapp.capture.NoticeSheet
import io.github.murtazanasir.mailr.notesapp.capture.SheetFrame
import io.github.murtazanasir.mailr.notesapp.data.WidgetTarget
import io.github.murtazanasir.mailr.notesapp.ui.NotesTheme
import kotlinx.coroutines.launch

/** Which widget is being set up, from its provider. */
enum class WidgetKind(val noun: String, @StringRes val whichRes: Int, @StringRes val noneRes: Int, @StringRes val labelRes: Int) {
    CHECKLIST("list", R.string.notesapp_widget_which_list, R.string.notesapp_widget_no_lists, R.string.notesapp_list),
    NOTE("note", R.string.notesapp_widget_which_note, R.string.notesapp_widget_no_notes, R.string.notesapp_note),
    TABLE("table", R.string.notesapp_widget_which_table, R.string.notesapp_widget_no_tables, R.string.notesapp_table),
    CAPTURE("list", R.string.notesapp_widget_which_list, R.string.notesapp_widget_no_lists, R.string.notesapp_list),
}

/**
 * The picker shown when a widget is placed and when it is re-picked (long-press → reconfigure, or a
 * tap on "Tap to choose"): a list for the checklist widget, a note for the note widget, a table and
 * up to two columns for the table widget, the default list for the capture widget's "Add to".
 */
class WidgetPickActivity : ComponentActivity() {
    // The app's language before Android 13; a no-op from 13 on.
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(io.github.murtazanasir.mailr.i18n.AppLocales.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        val appWidgetId = intent.getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID)
        setResult(Activity.RESULT_CANCELED, Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId))
        if (appWidgetId == AppWidgetManager.INVALID_APPWIDGET_ID) { finish(); return }
        val g = (application as NotesApplication).graph
        followHideContent(g.prefs)
        val kind = kindOf(AppWidgetManager.getInstance(this).getAppWidgetInfo(appWidgetId)?.provider?.className)
        setContent {
            val look by g.prefs.look.collectAsStateWithLifecycle()
            val session by g.sessions.session.collectAsStateWithLifecycle()
            NotesTheme(look) {
                if (session == null) NoticeSheet(stringResource(R.string.notesapp_sign_in_to_notes), stringResource(R.string.notesapp_widget_sign_in_first), onDone = ::finish)
                else WidgetPicker(g, kind, g.prefs.widget(appWidgetId)) { target ->
                    if (kind == WidgetKind.CAPTURE) g.prefs.defaultList = target?.id
                    else g.prefs.setWidget(appWidgetId, target)
                    g.scope.launch { catching { Widgets.updateAll(this@WidgetPickActivity) } }
                    setResult(Activity.RESULT_OK, Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId))
                    finish()
                }
            }
        }
    }

    companion object {
        fun kindOf(provider: String?): WidgetKind = when (provider?.substringAfterLast('.')) {
            ChecklistWidgetReceiver::class.java.simpleName -> WidgetKind.CHECKLIST
            NoteWidgetReceiver::class.java.simpleName -> WidgetKind.NOTE
            TableWidgetReceiver::class.java.simpleName -> WidgetKind.TABLE
            else -> WidgetKind.CAPTURE
        }
    }
}

/** A choice of note, list or table (and a table's columns); [onDone] with null clears the capture default. */
@Composable
fun WidgetPicker(g: NotesGraph, kind: WidgetKind, current: WidgetTarget?, onDone: (WidgetTarget?) -> Unit) {
    val notes by g.notes.repo.notes.collectAsState(emptyList())
    val tables by g.notes.repo.tables.collectAsState(emptyList())
    var picked by remember { mutableStateOf(current?.id) }
    var columns by remember { mutableStateOf(current?.columns.orEmpty()) }
    val scope = rememberCoroutineScope()
    val untitledList = stringResource(io.github.murtazanasir.mailr.notes.R.string.notes_untitled_list)
    val untitledNote = stringResource(io.github.murtazanasir.mailr.notes.R.string.notes_untitled_note)
    val untitledTable = stringResource(io.github.murtazanasir.mailr.notes.R.string.notes_untitled_table)
    val options: List<Pair<String, String>> = when (kind) {
        WidgetKind.CHECKLIST, WidgetKind.CAPTURE -> notes.filter { it.isList && it.deletedAt == null }.map { it.id to it.title.ifBlank { untitledList } }
        WidgetKind.NOTE -> notes.filter { it.deletedAt == null }.map { it.id to it.title.ifBlank { if (it.isList) untitledList else untitledNote } }
        WidgetKind.TABLE -> tables.filter { it.deletedAt == null }.map { it.id to it.name.ifBlank { untitledTable } }
    }.filter { !it.first.startsWith("local-") }
    SheetFrame({ onDone(current) }) {
        Text(stringResource(if (kind == WidgetKind.CAPTURE) R.string.notesapp_widget_add_which_list else kind.whichRes), style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.padding(bottom = 8.dp))
        if (options.isEmpty()) Text(stringResource(kind.noneRes))
        else Choices(stringResource(kind.labelRes), options, options.firstOrNull { it.first == picked }, { it.second },
            { picked = it.first; columns = emptyList() }, "pick")
        if (kind == WidgetKind.TABLE) {
            val t = tables.firstOrNull { it.id == picked }
            if (t != null) {
                val primary = Cells.primary(t)
                val cols = t.columns.filter { it.id != primary?.id }
                if (cols.isNotEmpty()) {
                    Text(stringResource(R.string.notesapp_widget_columns_beside, primary?.name ?: stringResource(R.string.notesapp_widget_the_title)), style = MaterialTheme.typography.labelLarge,
                        modifier = Modifier.padding(top = 8.dp))
                    androidx.compose.foundation.layout.FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        cols.forEach { c ->
                            androidx.compose.material3.FilterChip(selected = c.id in columns, onClick = {
                                columns = if (c.id in columns) columns - c.id else (columns + c.id).takeLast(2)
                            }, label = { Text(c.name) }, modifier = Modifier.testTag("column-${c.id}"))
                        }
                    }
                }
            }
        }
        Row(Modifier.fillMaxWidth().padding(top = 16.dp), horizontalArrangement = Arrangement.End) {
            TextButton(onClick = { onDone(current) }) { Text(stringResource(R.string.notesapp_cancel)) }
            Spacer(Modifier.width(8.dp))
            Button(enabled = picked != null, onClick = {
                scope.launch {
                    val id = picked ?: return@launch
                    onDone(WidgetTarget(when (kind) { WidgetKind.TABLE -> "table"; else -> "note" }, id, columns))
                }
            }, modifier = Modifier.testTag("pick-done")) { Text(stringResource(R.string.notesapp_done)) }
        }
    }
}
