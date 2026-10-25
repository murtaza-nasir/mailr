package io.github.murtazanasir.mailr.ui.calendar

import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Checklist
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
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
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.PrepPack
import io.github.murtazanasir.mailr.data.api.PrepSummary
import io.github.murtazanasir.mailr.ui.util.Format
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import java.time.format.DateTimeFormatter
import java.util.Locale

// The event detail's "Meeting prep" card (event.prep; android.calendar): for an event with guests
// that has not ended, the last recording and its open action items, open questions, threads
// waiting on the guests and what you promised them, recent threads, related tasks, memory notes
// and recent files. It loads when opened (at once from a reminder's Prep). Summarise asks the
// user's chat model on the server for one paragraph.

/** What the event detail needs to show the card; null leaves it out (previews, tests without a server). */
class PrepHooks(
    val load: suspend (uid: String, occurrence: String?) -> PrepPack,
    val summarise: suspend (uid: String, occurrence: String?) -> PrepSummary,
    val openConversation: (Long) -> Unit = {},
    val openTask: (String) -> Unit = {},
    val openRecording: (Long, Long) -> Unit = { _, _ -> },
    /** Opened from a reminder's Prep (mailr://event/…?prep=1). */
    val initiallyOpen: Boolean = false,
)

/** The event in the app with its meeting prep open: a reminder's Prep (notification and sheet). */
fun prepUri(uid: String, occurrence: String?): android.net.Uri = android.net.Uri.parse("mailr://event").buildUpon().appendPath(uid)
    .appendQueryParameter("occ", occurrence.orEmpty()).appendQueryParameter("prep", "1").build()

object MeetingPrepRules {
    /** An event offers prep while it has guests and has not ended more than half an hour ago. */
    fun offers(e: CalendarEvent, now: Long): Boolean {
        if (e.attendeeList.isEmpty()) return false
        val end = Format.parse(e.end ?: e.start)?.toInstant()?.toEpochMilli() ?: return true
        return end > now - 30 * 60_000L
    }
}

private fun day(s: String?): String =
    Format.parse(s)?.format(LocaleFormats.date("MMMd", "MMM d")) ?: ""

private sealed interface PrepLoad {
    data object Loading : PrepLoad
    data class Failed(val message: String) : PrepLoad
    data class Ready(val pack: PrepPack) : PrepLoad
}

// ACT event.prep @android.calendar
@Composable
fun MeetingPrepCard(e: CalendarEvent, hooks: PrepHooks) {
    var open by remember(e.uid, e.occurrence) { mutableStateOf(hooks.initiallyOpen) }
    val loadFailed = stringResource(R.string.cal_prep_load_failed)
    val load by produceState<PrepLoad>(PrepLoad.Loading, e.uid, e.occurrence, open) {
        if (!open) return@produceState
        value = try {
            PrepLoad.Ready(hooks.load(e.uid, e.occurrence))
        } catch (x: CancellationException) {
            throw x
        } catch (x: Exception) {
            PrepLoad.Failed(loadFailed)
        }
    }
    val title = stringResource(R.string.cal_prep_title)
    Surface(color = MaterialTheme.colorScheme.surfaceContainerHigh, shape = MaterialTheme.shapes.medium,
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp).semantics { contentDescription = title }) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(Modifier.fillMaxWidth().heightIn(min = 40.dp).clickable(onClickLabel = if (open) stringResource(R.string.cal_prep_hide) else stringResource(R.string.cal_prep_show)) { open = !open },
                verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.Checklist, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(8.dp))
                Text(title, style = MaterialTheme.typography.titleSmall, modifier = Modifier.semantics { heading() })
                val ready = (load as? PrepLoad.Ready)?.pack
                if (open && ready != null) Text(stringResource(R.string.cal_prep_counts, if (ready.empty) stringResource(R.string.cal_prep_nothing_yet) else ready.counts()),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1,
                    overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                Spacer(Modifier.weight(1f))
                Icon(if (open) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore, null)
            }
            if (open) when (val l = load) {
                PrepLoad.Loading -> Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(8.dp))
                    Text(stringResource(R.string.cal_prep_gathering), style = MaterialTheme.typography.bodySmall)
                }
                is PrepLoad.Failed -> Text(l.message, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                is PrepLoad.Ready -> PrepBody(e, l.pack, hooks)
            }
        }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold,
        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp).semantics { heading() })
}

@Composable
private fun Line(text: String, detail: String = "", onClick: (() -> Unit)? = null) {
    val m = Modifier.fillMaxWidth().heightIn(min = 32.dp).let { if (onClick != null) it.clickable(onClick = onClick) else it }
    Row(m.padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(text, style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis,
            color = if (onClick != null) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.weight(1f, fill = false))
        if (detail.isNotBlank()) Text(" · $detail", style = MaterialTheme.typography.bodySmall, maxLines = 1,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun PrepBody(e: CalendarEvent, p: PrepPack, hooks: PrepHooks) {
    val scope = rememberCoroutineScope()
    var summary by remember(e.uid, e.occurrence) { mutableStateOf<PrepSummary?>(null) }
    var busy by remember { mutableStateOf(false) }
    val summaryFailed = stringResource(R.string.cal_prep_summary_failed)
    if (p.empty) {
        Text(if (p.hidden > 0) stringResource(R.string.cal_prep_empty_hidden) else stringResource(R.string.cal_prep_empty),
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        return
    }
    summary?.let { s ->
        Text(if (s.status == "ok" && !s.text.isNullOrBlank()) s.text else s.message ?: summaryFailed,
            style = MaterialTheme.typography.bodyMedium,
            color = if (s.status == "ok") MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
    }
    if (summary?.status != "ok") TextButton(enabled = !busy, onClick = {
        busy = true
        scope.launch {
            summary = try {
                hooks.summarise(e.uid, e.occurrence)
            } catch (x: CancellationException) {
                throw x
            } catch (x: Exception) {
                PrepSummary("error", null, summaryFailed)
            }
            busy = false
        }
    }, modifier = Modifier.heightIn(min = 48.dp)) {
        Icon(Icons.Outlined.AutoAwesome, null, Modifier.size(18.dp))
        Spacer(Modifier.width(6.dp))
        Text(if (busy) stringResource(R.string.cal_prep_summarising) else stringResource(R.string.cal_prep_summarise))
    }
    p.recording?.let { r ->
        SectionTitle(stringResource(R.string.cal_prep_last_time))
        Line(r.title.ifBlank { stringResource(R.string.cal_prep_recording) }, day(r.meetingDate ?: r.createdAt)) { hooks.openRecording(r.connectorId, r.recordingId) }
        r.summaryLine?.takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
        val dueFmt = stringResource(R.string.cal_prep_due)
        r.actionItems.open.forEach { t -> Line(t.summary, t.due?.let { dueFmt.format(day(it)) } ?: "") { hooks.openTask(t.uid) } }
        val n = r.actionItems.open.size
        val openItems = pluralStringResource(R.plurals.cal_prep_open_action_items, n, n)
        Text(if (r.actionItems.done > 0) stringResource(R.string.cal_prep_action_items_done, openItems, r.actionItems.done) else openItems,
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    val oi = p.openItems
    if (oi.questions.isNotEmpty() || oi.waitingOn.isNotEmpty() || oi.promised.isNotEmpty()) {
        SectionTitle(stringResource(R.string.cal_prep_open_items))
        val asked = stringResource(R.string.cal_prep_asked)
        val waiting = stringResource(R.string.cal_prep_waiting)
        val since = stringResource(R.string.cal_prep_since)
        val noSubject = stringResource(R.string.cal_prep_no_subject)
        val youSaid = stringResource(R.string.cal_prep_you_said)
        oi.questions.forEach { q -> Line(asked.format(q.question), p.guestName(q.from)) { hooks.openConversation(q.conversationId) } }
        oi.waitingOn.forEach { w -> Line(waiting.format(w.subject.ifBlank { noSubject }), w.since?.let { since.format(day(it)) } ?: "") { hooks.openConversation(w.conversationId) } }
        oi.promised.forEach { c -> Line(youSaid.format(c.text)) { hooks.openConversation(c.conversationId) } }
    }
    if (p.threads.isNotEmpty()) {
        SectionTitle(stringResource(R.string.cal_prep_recent_threads))
        val noSubject = stringResource(R.string.cal_prep_no_subject)
        val wroteLast = stringResource(R.string.cal_prep_you_wrote_last)
        p.threads.forEach { t -> Line(t.subject.ifBlank { noSubject }, if (t.fromMe) wroteLast.format(day(t.lastDate)) else day(t.lastDate)) { hooks.openConversation(t.conversationId) } }
    }
    if (p.tasks.isNotEmpty()) {
        SectionTitle(stringResource(R.string.cal_prep_tasks))
        val dueFmt = stringResource(R.string.cal_prep_due)
        p.tasks.forEach { t -> Line(t.summary, t.due?.let { dueFmt.format(day(it)) } ?: "") { hooks.openTask(t.uid) } }
    }
    if (p.notes.isNotEmpty()) {
        SectionTitle(stringResource(R.string.cal_prep_memories))
        p.notes.forEach { n ->
            Text(n.entity.name + if (n.entity.summary.isNotBlank()) " · ${n.entity.summary}" else "", style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium)
            n.notes.forEach { m -> Text("– ${m.text}", style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(start = 8.dp)) }
        }
    }
    if (p.attachments.isNotEmpty()) {
        SectionTitle(stringResource(R.string.cal_prep_files))
        val youSent = stringResource(R.string.cal_prep_you_sent)
        p.attachments.forEach { a ->
            Line(a.filename, (if (a.direction == "sent") youSent else a.fromName.ifBlank { p.guestName(a.from) }) + " · " + day(a.date)) { hooks.openConversation(a.conversationId) }
        }
    }
    if (p.hidden > 0) Text(stringResource(R.string.cal_prep_hidden), style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant)
}
