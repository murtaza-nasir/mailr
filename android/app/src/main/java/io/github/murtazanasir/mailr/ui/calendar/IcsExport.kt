package io.github.murtazanasir.mailr.ui.calendar

import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import android.content.Context
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.cal.CalendarRepository
import io.github.murtazanasir.mailr.ui.graph
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * "Download a copy first" before a calendar or a task list is deleted:
 * the user picks where the .ics file goes (Storage Access Framework), then the collection's export
 * is written there.
 */
object IcsExport {
    /** The suggested file name: the collection's name, without characters a file system refuses. */
    fun fileName(name: String): String =
        name.replace(Regex("[\\\\/:*?\"<>|\\p{Cntrl}]"), "_").trim().trimStart('.').take(100).ifBlank { AppText.get(R.string.cal_export_default_name) } + ".ics"

    /** ACT event.export_ics @android.calendar @android.tasks: downloads [c] and writes it to [uri]. Returns the line the snackbar shows. */
    suspend fun save(context: Context, repo: CalendarRepository, c: CalendarCollection, uri: Uri): String = try {
        val bytes = repo.exportIcs(c)
        withContext(Dispatchers.IO) {
            context.contentResolver.openOutputStream(uri, "wt")?.use { it.write(bytes) } ?: error(AppText.get(R.string.cal_export_open_failed))
        }
        AppText.get(R.string.cal_export_saved, c.name)
    } catch (e: java.io.IOException) {
        AppText.get(R.string.cal_export_offline)
    } catch (e: Exception) {
        AppText.get(R.string.cal_export_failed, e.userMessage())
    }
}

/**
 * The file picker for [IcsExport]: call the returned function with a collection; [onDone] gets
 * the line to show when the copy is written (or why it was not).
 */
@Composable
fun rememberIcsExport(onDone: (String) -> Unit): (CalendarCollection) -> Unit {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var pending by remember { mutableStateOf<CalendarCollection?>(null) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/calendar")) { uri ->
        val c = pending
        pending = null
        if (uri != null && c != null) scope.launch { onDone(IcsExport.save(context, context.graph.calendar, c, uri)) }
    }
    return { c ->
        pending = c
        launcher.launch(IcsExport.fileName(c.name))
    }
}

/** The dialogs' "Download a copy" button (ACT event.export_ics). */
@Composable
fun DownloadCopyButton(c: CalendarCollection, export: (CalendarCollection) -> Unit) {
    if (c.calendarId != null) TextButton(onClick = { export(c) }) { Text(stringResource(R.string.cal_export_download_first)) }
}
