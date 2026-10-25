package io.github.murtazanasir.mailr.ui.recordings

import io.github.murtazanasir.mailr.i18n.LocaleFormats
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.OpenInNew
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.PlayCircle
import androidx.compose.material.icons.outlined.TaskAlt
import io.github.murtazanasir.mailr.look.AssistChip
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.Checkbox
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import io.github.murtazanasir.mailr.look.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.ActionItem
import io.github.murtazanasir.mailr.data.api.EventRef
import io.github.murtazanasir.mailr.data.api.Recording
import io.github.murtazanasir.mailr.ui.util.Format
import kotlinx.coroutines.launch
import java.time.format.DateTimeFormatter
import java.util.Locale

// Recordings in the event detail (android.calendar) and in the reader
// (android.reader), and the action-item review list.

/** A card in the colours of the recording surfaces. */
@Composable
private fun RecordingCard(label: String, content: @Composable () -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surfaceContainerHigh, shape = MaterialTheme.shapes.medium,
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp).semantics { contentDescription = label }) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) { content() }
    }
}

@Composable
private fun CardHead(icon: androidx.compose.ui.graphics.vector.ImageVector, title: String, detail: String, localOnly: Boolean = false) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Icon(icon, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.width(8.dp))
        Text(title, style = MaterialTheme.typography.titleSmall, modifier = Modifier.semantics { heading() })
        if (detail.isNotBlank()) Text(" · $detail", style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis,
            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f, fill = false))
        if (localOnly) { Spacer(Modifier.width(8.dp)); Pill(stringResource(R.string.rec_local_only), MaterialTheme.colorScheme.tertiaryContainer, MaterialTheme.colorScheme.onTertiaryContainer, lock = true) }
    }
}

/** A summary excerpt without Markdown marks. */
private fun plain(md: String) = md.replace(Regex("(?m)^#+\\s*"), "").replace(Regex("(?m)^\\s*[-*+]\\s+"), "• ").replace("**", "").trim()

/**
 * The event detail's Recording section: "Last time" for an upcoming meeting, then each recording
 * linked to (or suggested for) this occurrence with "Is this the recording of this meeting? Yes /
 * No", the summary's first lines, Open recording, Follow-up email and Add tasks. Nothing shows
 * without such a recording: no nagging.
 */
// ACT recording.view @android.reader @android.calendar, ACT recording.link @android.calendar, ACT recording.unlink @android.calendar, ACT recording.follow_up @android.reader @android.calendar, ACT recording.to_tasks @android.reader @android.calendar @android.recording,
// ACT event.last_recording @android.calendar
@Composable
fun EventRecordings(rec: Recordings, uid: String, occurrence: String?) {
    val last by rememberLoad(uid, occurrence) { rec.api.lastTime(uid, occurrence) }
    val list by rememberLoad(uid, occurrence, rec.version) { rec.api.eventRecordings(uid, occurrence) }
    Column(Modifier.fillMaxWidth()) {
        (last as? Load.Ready)?.value?.item?.let { LastTimeLine(rec, it) }
        (list as? Load.Ready)?.value?.items?.forEach { r -> EventRecording(rec, r, uid, occurrence) }
    }
}

// ACT event.last_recording: the recording of the last meeting in this series (or with this title
// and these people), with its summary's first line. A tap opens the sheet.
@Composable
private fun LastTimeLine(rec: Recordings, r: Recording) {
    Row(Modifier.fillMaxWidth().clickable(onClickLabel = stringResource(R.string.rec_open_last)) { rec.show(r.connectorId, r.recordingId) }
        .padding(horizontal = 20.dp, vertical = 10.dp).semantics(mergeDescendants = true) {}, verticalAlignment = Alignment.Top) {
        Icon(Icons.Outlined.History, null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(22.dp))
        Spacer(Modifier.width(16.dp))
        Column(Modifier.weight(1f)) {
            Text(buildString {
                append(stringResource(R.string.rec_last_time, r.displayTitle))
                Format.parse(r.meetingDate ?: r.createdAt)?.let { append(" · " + it.format(LocaleFormats.date("EEEMMMd", "EEE, MMM d"))) }
            }, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
            r.summaryLine?.takeIf { it.isNotBlank() }?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2,
                    overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

@Composable
private fun EventRecording(rec: Recordings, r: Recording, uid: String, occurrence: String?) {
    val link = r.link
    val occ = link?.occurrence ?: occurrence
    val ev = EventRef(uid, occ)
    RecordingCard(stringResource(R.string.rec_recording)) {
        CardHead(Icons.Outlined.PlayCircle, stringResource(R.string.rec_recording), listOf(r.title, fmtDuration(r.durationS)).filter { it.isNotBlank() }.joinToString(" · "),
            r.localOnly)
        if (link?.state == "suggested") {
            val question = stringResource(R.string.rec_is_this_meeting)
            val linked = stringResource(R.string.rec_linked_meeting)
            val dismissed = stringResource(R.string.rec_suggestion_dismissed)
            Column(Modifier.semantics(mergeDescendants = false) { contentDescription = question }) {
                Text(question, style = MaterialTheme.typography.bodyMedium)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilledTonalButton(onClick = { rec.link(r.connectorId, r.recordingId, "event", uid, occ, linked) },
                        modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.rec_yes)) }
                    TextButton(onClick = {
                        rec.unlink(link, dismissed) { rec.link(r.connectorId, r.recordingId, "event", uid, occ, linked) }
                    }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.rec_no)) }
                }
            }
        }
        r.summaryExcerpt?.takeIf { it.isNotBlank() }?.let {
            Text(plain(it), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 4,
                overflow = TextOverflow.Ellipsis)
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            AssistChip(onClick = { rec.show(r.connectorId, r.recordingId) }, label = { Text(stringResource(R.string.rec_open_recording)) },
                leadingIcon = { Icon(Icons.Outlined.PlayCircle, null) }, modifier = Modifier.heightIn(min = 48.dp))
            FollowUpButtons(rec, r.connectorId, r.recordingId, ev, enabled = r.hasSummary)
            AssistChip(onClick = { rec.showTasks(TasksTarget(r.connectorId, r.recordingId, r.displayTitle, ev)) },
                enabled = r.hasSummary || r.hasTranscript, label = { Text(stringResource(R.string.rec_add_tasks)) },
                leadingIcon = { Icon(Icons.Outlined.TaskAlt, null) }, modifier = Modifier.heightIn(min = 48.dp))
        }
    }
}

/**
 * The reader's recording card above a Speakr notification email: mailr recognises the email by its
 * link alone (the server's GET /speakr/mail-card), and everything shown comes from the mirror and
 * Speakr, never from the email. "Transcription failed" offers Open in Speakr only.
 */
// ACT recording.view, ACT recording.follow_up, ACT recording.to_tasks
@Composable
fun RecordingMailCard(rec: Recordings, messageId: Long) {
    val load by rememberLoad(messageId, rec.version) { rec.api.mailCard(messageId) }
    (load as? Load.Ready)?.value?.items?.forEach { r -> MailCardOne(rec, r) }
}

@Composable
private fun MailCardOne(rec: Recordings, r: Recording) {
    val ev = r.links.firstOrNull { it.kind == "event" }
    val evRef = ev?.let { EventRef(it.target, it.occurrence) }
    RecordingCard(stringResource(R.string.rec_recording)) {
        CardHead(if (r.failed) Icons.Outlined.ErrorOutline else Icons.Outlined.PlayCircle,
            stringResource(if (r.failed) R.string.rec_transcription_failed else if (r.processing) R.string.rec_processing else R.string.rec_recording),
            listOf(r.title, recordingWhen(r), fmtDuration(r.durationS)).filter { it.isNotBlank() }.joinToString(" · "), r.localOnly)
        if (ev != null) Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.CalendarMonth, null, Modifier.size(14.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.width(6.dp))
            Text(stringResource(if (ev.state == "suggested") R.string.rec_probably else R.string.rec_linked_to, ev.title ?: stringResource(R.string.rec_an_event)) +
                (Format.parse(ev.start)?.let { " · " + it.format(LocaleFormats.date("MMMd", "MMM d")) } ?: ""),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (!r.failed) r.summaryExcerpt?.takeIf { it.isNotBlank() }?.let {
            Text(plain(it), style = MaterialTheme.typography.bodySmall, maxLines = 3, overflow = TextOverflow.Ellipsis)
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (!r.failed) {
                AssistChip(onClick = { rec.show(r.connectorId, r.recordingId) }, label = { Text(stringResource(R.string.rec_open_recording)) },
                    leadingIcon = { Icon(Icons.Outlined.PlayCircle, null) }, modifier = Modifier.heightIn(min = 48.dp))
                FollowUpButtons(rec, r.connectorId, r.recordingId, evRef, enabled = r.hasSummary)
                AssistChip(onClick = { rec.showTasks(TasksTarget(r.connectorId, r.recordingId, r.displayTitle, evRef)) },
                    enabled = r.hasSummary || r.hasTranscript, label = { Text(stringResource(R.string.rec_add_tasks)) },
                    leadingIcon = { Icon(Icons.Outlined.TaskAlt, null) }, modifier = Modifier.heightIn(min = 48.dp))
            }
            // ACT recording.open_speakr @android.recording
            AssistChip(onClick = { rec.openUrl(r.url) }, label = { Text(stringResource(R.string.rec_open_speakr)) },
                leadingIcon = { Icon(Icons.AutoMirrored.Outlined.OpenInNew, null) }, modifier = Modifier.heightIn(min = 48.dp))
        }
    }
}

/**
 * "Add tasks": mailr's model proposes the meeting's action items; the user ticks which become
 * tasks (items owned by someone else start unticked, ones already added cannot be ticked). Each
 * task links to the recording, with Speakr's link at the moment in its notes.
 */
// ACT recording.to_tasks
@Composable
fun ActionItemsReview(rec: Recordings, target: TasksTarget) {
    val load by rememberLoad(target.cid, target.rid) { rec.api.actionItems(target.cid, target.rid) }
    val ticked = remember(target) { mutableStateMapOf<Int, Boolean>() }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    Column(Modifier.fillMaxWidth().padding(bottom = 16.dp)) {
        Text(stringResource(R.string.rec_tasks_from, target.title), style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp).semantics { heading() })
        when (val l = load) {
            Load.Loading -> Row(Modifier.padding(20.dp), verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp); Spacer(Modifier.width(10.dp))
                Text(stringResource(R.string.rec_reading_meeting), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            is Load.Failed -> Text(l.message, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(20.dp))
            is Load.Ready -> {
                val items = l.value.items
                fun on(i: Int, it: ActionItem) = (ticked[i] ?: it.checked) && !it.alreadyAdded
                val chosen = items.filterIndexed { i, it -> on(i, it) }
                if (items.isEmpty()) Text(l.value.note ?: stringResource(R.string.rec_no_action_items),
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(20.dp))
                Column(Modifier.fillMaxWidth().weight(1f, fill = false).verticalScroll(rememberScrollState())) {
                    items.forEachIndexed { i, it -> ItemRow(rec, it, on(i, it)) { v -> ticked[i] = v } }
                }
                Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = { rec.showTasks(null) }) { Text(stringResource(R.string.rec_cancel)) }
                    Spacer(Modifier.width(8.dp))
                    Button(onClick = { busy = true; scope.launch { rec.addTasks(target, chosen); busy = false } },
                        enabled = chosen.isNotEmpty() && !busy) {
                        Text(if (chosen.isEmpty()) stringResource(R.string.rec_add_tasks) else pluralStringResource(R.plurals.rec_add_n_tasks, chosen.size, chosen.size))
                    }
                }
            }
        }
    }
}

@Composable
private fun ItemRow(rec: Recordings, it: ActionItem, checked: Boolean, onCheck: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(enabled = !it.alreadyAdded) { onCheck(!checked) }.padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.Top) {
        Checkbox(checked = checked, onCheckedChange = if (it.alreadyAdded) null else onCheck, enabled = !it.alreadyAdded)
        Column(Modifier.weight(1f).padding(top = 12.dp)) {
            Text(it.text, style = MaterialTheme.typography.bodyLarge,
                color = if (it.alreadyAdded) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
            Row(verticalAlignment = Alignment.CenterVertically) {
                val me = stringResource(R.string.rec_owner_me)
                val noOwner = stringResource(R.string.rec_no_owner)
                val already = stringResource(R.string.rec_already_added)
                val due = it.due?.let { d -> stringResource(R.string.rec_due, d) }
                Text(listOfNotNull(if (it.mine) me else it.owner.ifBlank { noOwner }, due,
                    already.takeIf { _ -> it.alreadyAdded }).joinToString(" · "),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (it.t != null && it.url.isNotBlank()) {
                    TextButton(onClick = { rec.openUrl(it.url) }, modifier = Modifier.heightIn(min = 32.dp)) { Text(fmtT(it.t)) }
                }
            }
        }
    }
}
