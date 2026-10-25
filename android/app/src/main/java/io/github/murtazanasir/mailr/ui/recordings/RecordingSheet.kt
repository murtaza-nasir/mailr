package io.github.murtazanasir.mailr.ui.recordings

import io.github.murtazanasir.mailr.i18n.LocaleFormats
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.OpenInNew
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Folder
import androidx.compose.material.icons.outlined.Groups
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.automirrored.outlined.Label
import io.github.murtazanasir.mailr.look.FilterChip
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.MailOutline
import androidx.compose.material.icons.outlined.PlayCircle
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.TaskAlt
import io.github.murtazanasir.mailr.look.AssistChip
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import io.github.murtazanasir.mailr.look.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.PrimaryTabRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.EventRef
import io.github.murtazanasir.mailr.data.api.Recording
import io.github.murtazanasir.mailr.data.api.RecordingLink
import io.github.murtazanasir.mailr.data.api.Segment
import io.github.murtazanasir.mailr.ui.util.Format
import com.mikepenz.markdown.m3.Markdown
import com.mikepenz.markdown.m3.markdownColor
import com.mikepenz.markdown.m3.markdownTypography
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

// The recording sheet (android.recording): one Speakr recording, read through
// mailr. Header: title, meeting date and length, folder, status, "Local only" when the connector
// keeps recordings away from hosted models, participants and tags. Actions: Follow-up email (two
// modes), Add tasks, Ask assistant, Open in Speakr (or Play in Speakr at the cited moment). Tabs:
// Summary (Speakr's Markdown), Transcript (turns with times; a time opens Speakr there; the turn at
// the cited moment is marked), Notes, Links (events, emails and tasks, suggestions to confirm or
// dismiss, events at that time to link).

private enum class RecTab(@androidx.annotation.StringRes val label: Int) {
    SUMMARY(R.string.rec_tab_summary), TRANSCRIPT(R.string.rec_tab_transcript), NOTES(R.string.rec_tab_notes), LINKS(R.string.rec_tab_links)
}

/** "Thu, Oct 1, 10:00 AM" for a recording's meeting date (or upload time). */
internal fun recordingWhen(r: Recording, zone: ZoneId = ZoneId.systemDefault()): String {
    val v = Format.parse(r.meetingDate ?: r.createdAt)?.withZoneSameInstant(zone) ?: return ""
    return v.format(LocaleFormats.date("EEEMMMdhmma", "EEE, MMM d, h:mm a"))
}

/** The event a recording is linked to (confirmed first), for follow-ups and tasks. */
internal fun eventOf(r: Recording): EventRef? =
    r.links.filter { it.kind == "event" }.sortedBy { if (it.state == "confirmed") 0 else 1 }.firstOrNull()
        ?.let { EventRef(it.target, it.occurrence) }

/** The sheet and the action-item review list over any tab (App.kt hosts this). */
@Composable
fun RecordingSheets(rec: Recordings) {
    rec.sheet?.let { target ->
        ModalBottomSheet(onDismissRequest = rec::close, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
            RecordingView(rec, target.cid, target.rid, target.t, onClose = rec::close)
        }
    }
    rec.tasks?.let { t ->
        ModalBottomSheet(onDismissRequest = { rec.showTasks(null) }, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
            ActionItemsReview(rec, t)
        }
    }
}

// ACT recording.view @android.recording, ACT recording.open_speakr @android.recording
@Composable
fun RecordingView(rec: Recordings, cid: Long, rid: Long, t: Double?, onClose: (() -> Unit)? = null) {
    val load by rememberLoad(cid, rid, rec.version) { rec.api.recording(cid, rid) }
    var tab by rememberSaveable(cid, rid, t) { mutableStateOf(if (t != null) RecTab.TRANSCRIPT else RecTab.SUMMARY) }
    val r = (load as? Load.Ready)?.value
    // The transcript loads when its tab is first shown, then stays.
    var wantTranscript by rememberSaveable(cid, rid) { mutableStateOf(false) }
    if (tab == RecTab.TRANSCRIPT) wantTranscript = true
    val transcript by rememberLoad(cid, rid, wantTranscript) { if (wantTranscript) rec.api.transcript(cid, rid) else null }
    var find by rememberSaveable { mutableStateOf("") }
    var bubbles by rememberSaveable { mutableStateOf(false) }
    val list = rememberLazyListState()
    // A cited moment: its run comes into view once the transcript is there. The sheet's effect, not a
    // list item's: an item's effect starts again each time the item comes back into view, and it starts
    // inside the list's measure, where scrolling the list is not allowed.
    val citedRun = (transcript as? Load.Ready)?.value?.segments?.let { all ->
        val at = turnAt(all, t)
        runsOf(all.withIndex().toList()).indexOfFirst { r -> r.turns.any { it.index == at } }.takeIf { it >= 0 && at >= 0 }
    }
    LaunchedEffect(citedRun) {
        // Title, header, tabs and the find row come before the runs.
        if (tab == RecTab.TRANSCRIPT) citedRun?.let { list.scrollToItem(4 + it) }
    }
    // One list for the whole sheet: the header scrolls away and the transcript gets the screen.
    val sheetDesc = stringResource(R.string.rec_recording)
    LazyColumn(state = list, modifier = Modifier.fillMaxWidth().heightIn(min = 360.dp).semantics { contentDescription = sheetDesc },
        contentPadding = androidx.compose.foundation.layout.PaddingValues(bottom = 24.dp)) {
        item(key = "title") {
            Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.PlayCircle, null, tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(10.dp))
                Text(r?.displayTitle ?: stringResource(R.string.rec_recording), style = MaterialTheme.typography.titleLarge, maxLines = 2, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f).semantics { heading() })
                if (onClose != null) IconButton(onClick = onClose) { Icon(Icons.Outlined.Close, stringResource(R.string.rec_close)) }
            }
        }
        when (val l = load) {
            Load.Loading -> item(key = "wait") { Box(Modifier.fillMaxWidth().padding(32.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() } }
            is Load.Failed -> item(key = "error") { Text(l.message, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(20.dp)) }
            is Load.Ready -> {
                item(key = "header") { Header(rec, l.value, t) }
                stickyHeader(key = "tabs") {
                    Surface(color = MaterialTheme.colorScheme.surfaceContainerLow) {
                        PrimaryTabRow(selectedTabIndex = tab.ordinal, containerColor = MaterialTheme.colorScheme.surfaceContainerLow) {
                            RecTab.entries.forEach { x ->
                                Tab(selected = tab == x, onClick = { tab = x },
                                    text = { Text(if (x == RecTab.LINKS && l.value.links.isNotEmpty()) stringResource(R.string.rec_tab_count, stringResource(x.label), l.value.links.size) else stringResource(x.label)) })
                            }
                        }
                    }
                }
                when (tab) {
                    RecTab.SUMMARY -> item(key = "summary") { Prose(l.value.unavailable["summary"]?.let { stringResource(R.string.rec_speakr_no_answer, it) }, l.value.summary, stringResource(R.string.rec_no_summary)) }
                    RecTab.NOTES -> item(key = "notes") { Prose(l.value.unavailable["notes"]?.let { stringResource(R.string.rec_speakr_no_answer, it) }, l.value.notes, stringResource(R.string.rec_no_notes)) }
                    RecTab.LINKS -> item(key = "links") { LinksTab(rec, l.value) }
                    RecTab.TRANSCRIPT -> transcriptItems(rec, transcript, t, l.value.url, find, { find = it }, bubbles, { bubbles = it })
                }
            }
        }
    }
}

@Composable
private fun Header(rec: Recordings, r: Recording, t: Double?) {
    val cs = MaterialTheme.colorScheme
    val labels by rememberLoad(Unit) { rec.labels() }
    val (folders, tags) = (labels as? Load.Ready)?.value ?: (emptyList<io.github.murtazanasir.mailr.data.api.SpeakrLabel>() to emptyList())
    Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 4.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        // Speakr's metadata strip: the folder and tags in their colours, then the plain facts.
        if (r.folder != null || r.tags.isNotEmpty() || r.status != "completed" || r.localOnly)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                r.folder?.let { f ->
                    val c = labelColor(folders.firstOrNull { it.name == f }?.color, FOLDER_FALLBACK)
                    Chip(f, c, contrastOn(c), Icons.Outlined.Folder)
                }
                r.tags.forEach { name ->
                    val c = labelColor(tags.firstOrNull { it.name == name }?.color, TAG_FALLBACK)
                    Chip(name, c, contrastOn(c), Icons.AutoMirrored.Outlined.Label)
                }
                if (r.status != "completed") Pill(r.status.ifBlank { "processing" }.lowercase().replaceFirstChar { it.uppercase() },
                    cs.secondaryContainer, cs.onSecondaryContainer)
                if (r.localOnly) Pill(stringResource(R.string.rec_local_only), cs.tertiaryContainer, cs.onTertiaryContainer, lock = true)
            }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            MetaChip(Icons.Outlined.Groups, r.participants.joinToString(", ").ifBlank { stringResource(R.string.rec_no_participants) })
            MetaChip(Icons.Outlined.CalendarMonth, recordingWhen(r).ifBlank { stringResource(R.string.rec_no_date) })
            fmtDuration(r.durationS).takeIf { it.isNotEmpty() }?.let { MetaChip(Icons.Outlined.Schedule, it) }
        }
        if (r.localOnly) {
            Text(stringResource(R.string.rec_local_only_note), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            // Kept out of screenshots and Recents (Settings → Privacy).
            io.github.murtazanasir.mailr.ui.components.SensitiveScreen()
        }
        val ev = eventOf(r)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FollowUpButtons(rec, r.connectorId, r.recordingId, ev, enabled = r.hasSummary)
            // ACT recording.to_tasks @android.recording
            AssistChip(onClick = { rec.showTasks(TasksTarget(r.connectorId, r.recordingId, r.displayTitle, ev)) },
                enabled = r.hasSummary || r.hasTranscript, label = { Text(stringResource(R.string.rec_add_tasks)) },
                leadingIcon = { Icon(Icons.Outlined.TaskAlt, null) }, modifier = Modifier.heightIn(min = 48.dp))
            // ACT recording.ask @android.recording
            AssistChip(onClick = { rec.askAbout(r.connectorId, r.recordingId, r.title) }, label = { Text(stringResource(R.string.rec_ask_ambrose, io.github.murtazanasir.mailr.ui.assistantName())) },
                leadingIcon = { Icon(Icons.Outlined.AutoAwesome, null) }, modifier = Modifier.heightIn(min = 48.dp))
            // ACT recording.open_speakr
            AssistChip(onClick = { rec.openUrl(speakrAt(r.url, t)) },
                label = { Text(if (t != null) stringResource(R.string.rec_play_at, fmtT(t)) else stringResource(R.string.rec_open_speakr)) },
                leadingIcon = { Icon(Icons.AutoMirrored.Outlined.OpenInNew, null) }, modifier = Modifier.heightIn(min = 48.dp))
        }
    }
}

/** A filled chip in a folder's or tag's colour (Speakr's .chip). */
@Composable
private fun Chip(text: String, bg: androidx.compose.ui.graphics.Color, fg: androidx.compose.ui.graphics.Color,
                 icon: androidx.compose.ui.graphics.vector.ImageVector) {
    Surface(color = bg, contentColor = fg, shape = RoundedCornerShape(6.dp)) {
        Row(Modifier.padding(start = 7.dp, end = 8.dp, top = 3.dp, bottom = 3.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, null, Modifier.size(13.dp)); Spacer(Modifier.width(5.dp))
            Text(text, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
        }
    }
}

/** A grey fact chip: people, date, length (Speakr's chip-meta). */
@Composable
private fun MetaChip(icon: androidx.compose.ui.graphics.vector.ImageVector, text: String) {
    Surface(color = MaterialTheme.colorScheme.surfaceContainerHighest, contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
        shape = RoundedCornerShape(6.dp)) {
        Row(Modifier.padding(horizontal = 8.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, null, Modifier.size(14.dp)); Spacer(Modifier.width(5.dp))
            Text(text, style = MaterialTheme.typography.labelMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
    }
}

@Composable
internal fun Pill(text: String, bg: androidx.compose.ui.graphics.Color, fg: androidx.compose.ui.graphics.Color, lock: Boolean = false) {
    Surface(color = bg, contentColor = fg, shape = RoundedCornerShape(8.dp)) {
        Row(Modifier.padding(horizontal = 8.dp, vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
            if (lock) { Icon(Icons.Outlined.Lock, null, Modifier.size(12.dp)); Spacer(Modifier.width(4.dp)) }
            Text(text, style = MaterialTheme.typography.labelMedium)
        }
    }
}

/** "Follow-up email" (Speakr's summary as written) and "Draft with assistant". ACT recording.follow_up @android.recording */
@Composable
internal fun FollowUpButtons(rec: Recordings, cid: Long, rid: Long, ev: EventRef?, enabled: Boolean) {
    val busy = rec.drafting
    AssistChip(onClick = { rec.followUp(cid, rid, "summary", ev) }, enabled = enabled && busy == null,
        label = { Text(stringResource(R.string.rec_follow_up)) },
        leadingIcon = {
            if (busy == "summary") CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
            else Icon(Icons.Outlined.MailOutline, null)
        }, modifier = Modifier.heightIn(min = 48.dp))
    AssistChip(onClick = { rec.followUp(cid, rid, "assistant", ev) }, enabled = enabled && busy == null,
        label = { Text(stringResource(R.string.rec_draft_assistant, io.github.murtazanasir.mailr.ui.assistantName())) },
        leadingIcon = {
            if (busy == "assistant") CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
            else Icon(Icons.Outlined.AutoAwesome, null)
        }, modifier = Modifier.heightIn(min = 48.dp))
}

@Composable
private fun Prose(error: String?, md: String?, empty: String) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp)) {
        when {
            error != null -> Text(error, color = MaterialTheme.colorScheme.onSurfaceVariant)
            md.isNullOrBlank() -> Text(empty, color = MaterialTheme.colorScheme.onSurfaceVariant)
            // Speakr's Markdown; images never load (as in assistant answers).
            else -> Markdown(md.replace(Regex("""!\[([^\]\n]{0,300})]\(([^)\s]{1,2000})[^)]*\)"""), stringResource(R.string.rec_md_image)),
                colors = markdownColor(), typography = markdownTypography(), modifier = Modifier.fillMaxWidth())
        }
    }
}

/** The turn being said at [t]: the last one that starts by then (a second of slack). */
internal fun turnAt(segs: List<Segment>, t: Double?): Int {
    if (t == null) return -1
    var best = -1
    segs.forEachIndexed { i, s -> if (s.t != null && s.t <= t + 1) best = i }
    return best
}

/** Speakr's transcript time: "Start" under half a second, then mm:ss or h:mm:ss. */
internal fun transcriptTime(t: Double?): String = when {
    t == null -> ""
    t < 0.5 -> AppText.get(R.string.rec_start)
    else -> clock((t * 1000).toLong())
}

/** Consecutive turns of one speaker, shown under one speaker tablet. */
internal data class Run(val speaker: String, val turns: List<IndexedValue<Segment>>)

internal fun runsOf(turns: List<IndexedValue<Segment>>): List<Run> {
    val out = mutableListOf<Run>()
    turns.forEach { iv ->
        val last = out.lastOrNull()
        if (last != null && last.speaker == iv.value.speaker) out[out.size - 1] = last.copy(turns = last.turns + iv)
        else out += Run(iv.value.speaker, listOf(iv))
    }
    return out
}

/** The transcript as items of the sheet's list: find, the bubble switch, then the speaker runs. */
private fun androidx.compose.foundation.lazy.LazyListScope.transcriptItems(
    rec: Recordings, load: Load<io.github.murtazanasir.mailr.data.api.Transcript?>, t: Double?, url: String,
    find: String, setFind: (String) -> Unit, bubbles: Boolean, setBubbles: (Boolean) -> Unit,
) {
    when (load) {
        Load.Loading -> item(key = "t-wait") {
            Row(Modifier.padding(20.dp), verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp); Spacer(Modifier.width(10.dp))
                Text(stringResource(R.string.rec_loading_transcript), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        is Load.Failed -> item(key = "t-error") { Text(load.message, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(20.dp)) }
        is Load.Ready -> {
            val all = load.value?.segments.orEmpty()
            val at = turnAt(all, t)
            val colors = speakerIndex(all.map { it.speaker })
            val words = find.lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }
            val shown = all.withIndex().filter { (_, s) -> words.all { w -> w in s.text.lowercase() || w in s.speaker.lowercase() } }
            val runs = runsOf(shown)
            item(key = "t-find") {
                Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(find, setFind, placeholder = { Text(stringResource(R.string.rec_find)) }, singleLine = true,
                        leadingIcon = { Icon(Icons.Outlined.Search, null) }, modifier = Modifier.weight(1f))
                    Spacer(Modifier.width(8.dp))
                    FilterChip(bubbles, { setBubbles(!bubbles) }, label = { Text(stringResource(R.string.rec_bubbles)) })
                }
                if (load.value?.truncated == true) Text(stringResource(R.string.rec_truncated),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 20.dp))
                if (shown.isEmpty()) Text(stringResource(if (find.isNotBlank()) R.string.rec_nothing_matches else R.string.rec_no_transcript),
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(20.dp))
            }
            runs.forEach { r ->
                item(key = "t-${r.turns.first().index}") {
                    Box(Modifier.padding(horizontal = 16.dp)) { RunView(rec, r, colors[r.speaker] ?: 0, at, url, bubbles) }
                }
            }
        }
    }
}

/**
 * One speaker's run: the speaker tablet in the speaker's colour, its left half the time (a tap
 * plays from there in Speakr), then each turn (simple) or a bubble per turn. The turn at the
 * cited moment is marked.
 */
@Composable
private fun RunView(rec: Recordings, r: Run, colorIndex: Int, at: Int, url: String, bubbles: Boolean) {
    val sc = speakerColor(colorIndex)
    val first = r.turns.first().value
    Column(Modifier.fillMaxWidth().padding(top = 12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            val time = transcriptTime(first.t)
            if (time.isNotEmpty()) Surface(color = sc.fg.copy(alpha = 0.85f), contentColor = sc.bg,
                shape = RoundedCornerShape(topStart = 6.dp, bottomStart = 6.dp),
                modifier = Modifier.heightIn(min = 28.dp).clickable(onClickLabel = stringResource(R.string.rec_play_from, time)) { rec.openUrl(speakrAt(url, first.t)) }) {
                Box(Modifier.padding(horizontal = 8.dp, vertical = 5.dp)) {
                    Text(time, style = MaterialTheme.typography.labelSmall.copy(fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace))
                }
            }
            if (r.speaker.isNotBlank()) Surface(color = sc.bg, contentColor = sc.fg,
                shape = if (time.isNotEmpty()) RoundedCornerShape(topEnd = 6.dp, bottomEnd = 6.dp) else RoundedCornerShape(6.dp),
                modifier = Modifier.heightIn(min = 28.dp)) {
                Box(Modifier.padding(horizontal = 8.dp, vertical = 5.dp)) {
                    Text(r.speaker, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
                }
            }
        }
        r.turns.forEach { (i, s) ->
            val marked = i == at
            val later = s !== first && s.t != null
            val body: @Composable () -> Unit = {
                Text(s.text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(horizontal = if (bubbles) 12.dp else 0.dp,
                    vertical = if (bubbles) 8.dp else 2.dp))
            }
            Row(Modifier.fillMaxWidth().semantics { selected = marked }, verticalAlignment = Alignment.Top) {
                Surface(color = when { marked -> MaterialTheme.colorScheme.secondaryContainer; bubbles -> sc.bg; else -> androidx.compose.ui.graphics.Color.Transparent },
                    contentColor = if (bubbles && !marked) sc.fg else MaterialTheme.colorScheme.onSurface,
                    shape = if (bubbles) RoundedCornerShape(12.dp, 12.dp, 12.dp, 4.dp) else RoundedCornerShape(6.dp),
                    shadowElevation = if (bubbles) 1.dp else 0.dp,
                    modifier = Modifier.weight(1f, fill = !bubbles).padding(end = if (bubbles) 40.dp else 0.dp)) {
                    Column(Modifier.padding(horizontal = if (marked && !bubbles) 6.dp else 0.dp)) { body() }
                }
                // Later turns in a run keep their own time, small, for a tap into Speakr.
                if (later) Text(transcriptTime(s.t), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(start = 8.dp, top = 4.dp).clickable(onClickLabel = stringResource(R.string.rec_play_from, transcriptTime(s.t))) {
                        rec.openUrl(speakrAt(url, s.t))
                    })
            }
        }
    }
}

private fun linkLabel(l: RecordingLink) = l.title ?: when (l.kind) {
    "event" -> AppText.get(R.string.rec_link_event); "task" -> AppText.get(R.string.rec_link_task); else -> AppText.get(R.string.rec_link_conversation)
}

// ACT recording.link @android.recording, ACT recording.unlink @android.recording
@Composable
private fun LinksTab(rec: Recordings, r: Recording) {
    val cs = MaterialTheme.colorScheme
    Column(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
        if (r.links.isEmpty()) Text(stringResource(R.string.rec_not_linked), color = cs.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp))
        val restored = stringResource(R.string.rec_link_restored)
        val linked = stringResource(R.string.rec_linked_event)
        val dismissed = stringResource(R.string.rec_suggestion_dismissed)
        val removed = stringResource(R.string.rec_link_removed)
        r.links.forEach { l ->
            val relink = { rec.link(r.connectorId, r.recordingId, l.kind, l.target, l.occurrence, restored) }
            Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp).heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(when (l.kind) { "event" -> Icons.Outlined.CalendarMonth; "task" -> Icons.Outlined.TaskAlt; else -> Icons.Outlined.MailOutline },
                    null, tint = cs.onSurfaceVariant, modifier = Modifier.padding(start = 8.dp))
                TextButton(onClick = { rec.close(); rec.openLink(l) }, modifier = Modifier.weight(1f)) {
                    Text(linkLabel(l) + (Format.parse(l.start)?.let { " · " + it.format(LocaleFormats.date("MMMd", "MMM d")) } ?: ""),
                        maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.fillMaxWidth())
                }
                if (l.state == "suggested") {
                    Text(stringResource(R.string.rec_suggested), style = MaterialTheme.typography.labelMedium, color = cs.onSurfaceVariant)
                    TextButton(onClick = { rec.link(r.connectorId, r.recordingId, l.kind, l.target, l.occurrence, linked) }) { Text(stringResource(R.string.rec_confirm)) }
                    TextButton(onClick = { rec.unlink(l, dismissed, relink) }) { Text(stringResource(R.string.rec_dismiss)) }
                } else TextButton(onClick = { rec.unlink(l, removed, relink) }) { Text(stringResource(R.string.rec_remove)) }
            }
        }
        if (r.candidates.isNotEmpty()) {
            Text(stringResource(R.string.rec_events_then), style = MaterialTheme.typography.titleSmall, color = cs.primary,
                modifier = Modifier.padding(start = 20.dp, top = 12.dp, bottom = 4.dp))
            r.candidates.forEach { c ->
                Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp).heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Outlined.CalendarMonth, null, tint = cs.onSurfaceVariant)
                    Spacer(Modifier.width(12.dp))
                    Text(c.summary.ifBlank { stringResource(R.string.rec_no_title) } + (Format.parse(c.start)?.withZoneSameInstant(ZoneId.systemDefault())
                        ?.let { " · " + it.format(LocaleFormats.date("hmma", "h:mm a")) } ?: ""),
                        maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                    FilledTonalButton(onClick = { rec.link(r.connectorId, r.recordingId, "event", c.uid, c.occurrence, linked) }) {
                        Text(stringResource(R.string.rec_link))
                    }
                }
            }
        }
    }
}
