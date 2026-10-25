package io.github.murtazanasir.mailr.ui.recordings

import io.github.murtazanasir.mailr.i18n.LocaleFormats
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.CloudDone
import androidx.compose.material.icons.outlined.CloudUpload
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.Folder
import androidx.compose.material.icons.outlined.Schedule
import io.github.murtazanasir.mailr.look.Button
import androidx.compose.material3.ButtonDefaults
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.SelectField
import io.github.murtazanasir.mailr.look.look
import androidx.compose.material3.LocalContentColor
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import io.github.murtazanasir.mailr.look.FilledTonalButton
import io.github.murtazanasir.mailr.look.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import io.github.murtazanasir.mailr.look.LinearProgressIndicator
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedButton
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.Scaffold
import io.github.murtazanasir.mailr.look.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import io.github.murtazanasir.mailr.look.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.SpeakrLabel
import io.github.murtazanasir.mailr.data.recorder.RecState
import io.github.murtazanasir.mailr.data.recorder.RecordingMeta
import io.github.murtazanasir.mailr.data.recorder.RecordingSession
import io.github.murtazanasir.mailr.ui.util.Format
import com.mohamedrejeb.richeditor.model.RichTextState
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.debounce
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

// New recording (android.recording): record a meeting on the phone and
// upload it to the user's Speakr through mailr. Before recording: the title, the event it belongs
// to (the current or next one, its guests as participants), the meeting date, Speakr's folder and
// tags, and Speakr's transcription options (language, speakers, hotwords, initial prompt). While
// recording: the time, a level meter, Pause or Resume, Stop, and notes as rich text kept as
// Markdown. On Stop it uploads, with progress, and links to the recording sheet. The microphone is
// asked for only when recording starts.

/** An event the recording can belong to. */
data class EventOption(val uid: String, val occurrence: String?, val title: String, val start: ZonedDateTime?,
                       val end: ZonedDateTime?, val participants: List<String>)

fun eventOption(e: CalendarEvent, me: Set<String>, zone: ZoneId = ZoneId.systemDefault()): EventOption = EventOption(
    e.uid, e.occurrence, e.summary.ifBlank { AppText.get(R.string.rec_no_title) }, Format.parse(e.start)?.withZoneSameInstant(zone),
    e.end?.let { Format.parse(it)?.withZoneSameInstant(zone) },
    e.attendeeList.filter { it.email.lowercase() !in me }.map { it.name.ifBlank { it.email } })

/** Today's timed events, and the one to start with: the current one, else the next. */
fun eventOptions(events: List<CalendarEvent>, me: Set<String>, now: ZonedDateTime): Pair<List<EventOption>, EventOption?> {
    val opts = events.filter { !it.allDay && !it.cancelled }.map { eventOption(it, me, now.zone) }
        .filter { it.start != null }.sortedBy { it.start }
    val current = opts.firstOrNull { it.start!! <= now && (it.end ?: it.start.plusHours(1)) > now }
    val next = opts.firstOrNull { it.start!! > now }
    return opts to (current ?: next)
}

/** The form of the New recording screen (it becomes [RecordingMeta]). */
class RecordingForm(now: ZonedDateTime) {
    var title by mutableStateOf("")
    var participants by mutableStateOf("")
    var meetingDate by mutableStateOf(now)
    var event by mutableStateOf<EventOption?>(null)
    var folderId by mutableStateOf<Long?>(null)
    val tagIds = mutableStateListOf<Long>()
    var language by mutableStateOf<String?>(null)
    var exactSpeakers by mutableStateOf(false)
    var minSpeakers by mutableStateOf("")
    var maxSpeakers by mutableStateOf("")
    var hotwords by mutableStateOf("")
    var initialPrompt by mutableStateOf("")

    /** Link the recording to [e] (or none): its title and guests fill the fields left as the last event set them. */
    fun pick(e: EventOption?) {
        val old = event
        event = e
        if (title.isBlank() || title == old?.title) title = e?.title.orEmpty()
        val oldPeople = old?.participants?.joinToString(", ").orEmpty()
        if (participants.isBlank() || participants == oldPeople) participants = e?.participants?.joinToString(", ").orEmpty()
        e?.start?.let { meetingDate = it }
    }

    /** A tag's ASR defaults fill the options that are still empty (the first tag picked wins, as in Speakr). */
    fun toggleTag(t: SpeakrLabel) {
        if (t.id in tagIds) { tagIds.remove(t.id); return }
        tagIds.add(t.id)
        if (tagIds.size == 1) {
            if (language == null) language = t.defaultLanguage?.ifBlank { null }
            if (minSpeakers.isBlank()) t.defaultMinSpeakers?.let { minSpeakers = it.toString() }
            if (maxSpeakers.isBlank()) t.defaultMaxSpeakers?.let { maxSpeakers = it.toString() }
            if (hotwords.isBlank()) hotwords = t.defaultHotwords.orEmpty()
            if (initialPrompt.isBlank()) initialPrompt = t.defaultInitialPrompt.orEmpty()
        }
    }

    fun meta(notes: String = ""): RecordingMeta {
        val min = minSpeakers.toIntOrNull()?.coerceIn(1, 20)
        val max = if (exactSpeakers) min else maxSpeakers.toIntOrNull()?.coerceIn(1, 20)
        return RecordingMeta(
            title = title.trim(), participants = participants.split(',').map { it.trim() }.filter { it.isNotEmpty() },
            meetingDate = meetingDate.toOffsetDateTime().toString(), folderId = folderId, tagIds = tagIds.toList(),
            language = language, minSpeakers = min, maxSpeakers = max, hotwords = hotwords.trim(), initialPrompt = initialPrompt.trim(),
            eventUid = event?.uid, occurrence = event?.occurrence, eventTitle = event?.title, notes = notes)
    }

    fun load(m: RecordingMeta, events: List<EventOption>) {
        title = m.title; participants = m.participants.joinToString(", ")
        Format.parse(m.meetingDate)?.let { meetingDate = it }
        event = events.firstOrNull { it.uid == m.eventUid } ?: m.eventUid?.let { EventOption(it, m.occurrence, m.eventTitle ?: AppText.get(R.string.rec_event), null, null, emptyList()) }
        folderId = m.folderId; tagIds.clear(); tagIds.addAll(m.tagIds); language = m.language
        exactSpeakers = m.minSpeakers != null && m.minSpeakers == m.maxSpeakers
        minSpeakers = m.minSpeakers?.toString().orEmpty(); maxSpeakers = m.maxSpeakers?.toString().orEmpty()
        hotwords = m.hotwords; initialPrompt = m.initialPrompt
    }
}

/** The Whisper languages Speakr offers (speakr static/js/asr-languages.js), named in the phone's language. */
val ASR_LANGUAGES: List<Pair<String, String>>
    get() = asrLanguages.getOrPut(Locale.getDefault()) { asrLanguages(Locale.getDefault()) }

private val asrLanguages = java.util.concurrent.ConcurrentHashMap<Locale, List<Pair<String, String>>>()

private fun asrLanguages(locale: Locale): List<Pair<String, String>> = listOf(
    "en", "zh", "de", "es", "ru", "ko", "fr", "ja", "pt", "tr", "pl", "ca", "nl", "ar", "sv", "it", "id", "hi", "fi", "vi", "he",
    "uk", "el", "ms", "cs", "ro", "da", "hu", "ta", "no", "th", "ur", "hr", "bg", "lt", "la", "mi", "ml", "cy", "sk", "te", "fa",
    "lv", "bn", "sr", "az", "sl", "kn", "et", "mk", "br", "eu", "is", "hy", "ne", "mn", "bs", "kk", "sq", "sw", "gl", "mr", "pa",
    "si", "km", "sn", "yo", "so", "af", "oc", "ka", "be", "tg", "sd", "gu", "am", "yi", "lo", "uz", "fo", "ht", "ps", "tk", "nn",
    "mt", "sa", "lb", "my", "bo", "tl", "mg", "as", "tt", "haw", "ln", "ha", "ba", "jw", "su", "yue",
).map { code ->
    val name = when (code) {
        "yue" -> AppText.get(R.string.rec_lang_cantonese)
        "haw" -> AppText.get(R.string.rec_lang_hawaiian)
        "jw" -> AppText.get(R.string.rec_lang_javanese)
        else -> Locale(code).getDisplayLanguage(locale)
    }
    code to name.replaceFirstChar { it.titlecase(locale) }
}.sortedBy { it.second }

/** What the screen asks of the app. */
data class RecorderCallbacks(
    val back: () -> Unit,
    val start: (RecordingMeta) -> Unit,
    val pause: () -> Unit,
    val resume: () -> Unit,
    /** Stop with the final notes (Markdown). */
    val stop: (String) -> Unit,
    val notes: (String) -> Unit,
    val meta: (RecordingMeta) -> Unit,
    val pickDate: () -> Unit,
    val openRecording: (Long, Long) -> Unit,
    val retry: (String) -> Unit,
    val discard: (String) -> Unit,
    val newOne: () -> Unit,
    val select: (String) -> Unit,
)

/** Bytes of audio for [ms] at the recorder's 64 kbit/s. */
fun estimatedSize(ms: Long): String {
    val bytes = ms / 1000.0 * 64_000 / 8
    return if (bytes >= 1_000_000) AppText.get(R.string.rec_size_mb, String.format(Locale.US, "%.1f", bytes / 1_000_000))
        else AppText.get(R.string.rec_size_kb, (bytes / 1000).toInt())
}

// ACT recording.record @android.recording
@OptIn(kotlinx.coroutines.FlowPreview::class)
@Composable
fun NewRecordingScreen(
    form: RecordingForm,
    session: RecordingSession?,
    folders: List<SpeakrLabel>,
    tags: List<SpeakrLabel>,
    events: List<EventOption>,
    level: Float,
    progress: Float?,
    waiting: List<RecordingSession>,
    cb: RecorderCallbacks,
    error: String? = null,
    timeSource: () -> Long = System::currentTimeMillis,  // clock-ok: stopwatch (elapsed time)
    levelHistory: List<Float>? = null,
) {
    val ctx = LocalContext.current
    val live = session?.live == true
    var permissionDenied by remember { mutableStateOf(false) }
    val askMic = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        permissionDenied = !ok
        if (ok) cb.start(form.meta())
    }
    fun record() {
        if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) cb.start(form.meta())
        else askMic.launch(Manifest.permission.RECORD_AUDIO)
    }
    // Details edited while recording go with the upload.
    LaunchedEffect(live) {
        if (live) snapshotFlow { form.meta() }.debounce(500).collect { cb.meta(it) }
    }
    Scaffold(
        contentWindowInsets = WindowInsets(0),
        topBar = {
            TopAppBar(title = { Text(stringResource(R.string.rec_new_title)) },
                navigationIcon = { IconButton(onClick = cb.back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.rec_back)) } })
        },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().imePadding(), horizontalAlignment = Alignment.CenterHorizontally) {
            Column(Modifier.weight(1f).widthIn(max = 720.dp).fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp)) {
                when {
                    session == null -> {
                        Details(form, folders, tags, events, cb, expandedAtFirst = true)
                        waiting.forEach { WaitingRow(it, cb) }
                    }
                    live -> Live(session, form, folders, tags, events, level, levelHistory, cb, timeSource)
                    else -> Finished(session, progress, cb)
                }
                if (permissionDenied) Text(stringResource(R.string.rec_mic_denied),
                    color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(vertical = 8.dp))
                error?.let { Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(vertical = 8.dp)) }
                Spacer(Modifier.height(16.dp))
            }
            // The controls stay at the bottom, in thumb reach.
            Surface(tonalElevation = 3.dp, modifier = Modifier.fillMaxWidth()) {
                Row(Modifier.widthIn(max = 720.dp).fillMaxWidth().navigationBarsPadding().padding(16.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterHorizontally), verticalAlignment = Alignment.CenterVertically) {
                    when {
                        session == null -> Button(onClick = ::record, modifier = Modifier.heightIn(min = 56.dp).fillMaxWidth(),
                            colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error, contentColor = MaterialTheme.colorScheme.onError)) {
                            Icon(Icons.Filled.Mic, null); Spacer(Modifier.width(8.dp)); Text(stringResource(R.string.rec_record), style = MaterialTheme.typography.titleMedium)
                        }
                        live -> {
                            val state = rememberNotesState(session)
                            if (session.state == RecState.RECORDING) FilledTonalButton(onClick = cb.pause, modifier = Modifier.heightIn(min = 56.dp).weight(1f)) {
                                Icon(Icons.Filled.Pause, null); Spacer(Modifier.width(8.dp)); Text(stringResource(R.string.rec_pause))
                            } else FilledTonalButton(onClick = cb.resume, modifier = Modifier.heightIn(min = 56.dp).weight(1f)) {
                                Icon(Icons.Filled.PlayArrow, null); Spacer(Modifier.width(8.dp)); Text(stringResource(R.string.rec_resume))
                            }
                            Button(onClick = { cb.stop(NotesMarkdown.read(state)) }, modifier = Modifier.heightIn(min = 56.dp).weight(1f),
                                colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error, contentColor = MaterialTheme.colorScheme.onError)) {
                                Icon(Icons.Filled.Stop, null); Spacer(Modifier.width(8.dp)); Text(stringResource(R.string.rec_stop_upload))
                            }
                        }
                        else -> OutlinedButton(onClick = cb.newOne, modifier = Modifier.heightIn(min = 56.dp).fillMaxWidth()) {
                            Icon(Icons.Filled.Mic, null); Spacer(Modifier.width(8.dp)); Text(stringResource(R.string.rec_new_title))
                        }
                    }
                }
            }
        }
    }
}

/** The notes editor's state for [session], filled once from its saved notes. */
private val notesStates = HashMap<String, RichTextState>()

@Composable
private fun rememberNotesState(session: RecordingSession): RichTextState =
    remember(session.id) { notesStates.getOrPut(session.id) { RichTextState().also { NotesMarkdown.write(it, session.meta.notes) } } }

@Composable
private fun Live(session: RecordingSession, form: RecordingForm, folders: List<SpeakrLabel>, tags: List<SpeakrLabel>, events: List<EventOption>,
                 level: Float, levelHistory: List<Float>?, cb: RecorderCallbacks, timeSource: () -> Long) {
    val now by produceState(timeSource(), session.state) {
        while (session.state == RecState.RECORDING) { value = timeSource(); delay(250) }
        value = timeSource()
    }
    val elapsed = session.elapsedAt(now)
    val recording = session.state == RecState.RECORDING
    val history = remember(session.id) { mutableStateListOf<Float>() }
    LaunchedEffect(level, recording) { if (recording && levelHistory == null) { history.add(level); while (history.size > 64) history.removeAt(0) } }
    val recordedDesc = stringResource(R.string.rec_recorded_desc, clock(elapsed))
    Column(Modifier.fillMaxWidth().padding(vertical = 12.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(clock(elapsed), style = MaterialTheme.typography.displayMedium.copy(fontFamily = FontFamily.Monospace),
            color = if (recording) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.semantics { contentDescription = recordedDesc })
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite }) {
            if (recording) Box(Modifier.size(10.dp).background(MaterialTheme.colorScheme.error, CircleShape))
            Spacer(Modifier.width(8.dp))
            Text(stringResource(if (recording) R.string.rec_recording else R.string.rec_paused), style = MaterialTheme.typography.titleSmall)
        }
        LevelMeter(levelHistory ?: history, recording, Modifier.fillMaxWidth().height(56.dp).padding(vertical = 8.dp))
        Text(stringResource(R.string.rec_estimated_size, estimatedSize(elapsed)), style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    val state = rememberNotesState(session)
    Text(stringResource(R.string.rec_notes), style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(top = 4.dp, bottom = 4.dp).semantics { heading() })
    NotesEditor(state, elapsedMs = { session.elapsedAt(timeSource()) }, onMarkdown = cb.notes)
    Spacer(Modifier.height(12.dp))
    Details(form, folders, tags, events, cb, expandedAtFirst = false)
}

/** The level meter: the last few seconds of loudness as bars, newest on the right. */
@Composable
fun LevelMeter(levels: List<Float>, active: Boolean, modifier: Modifier = Modifier) {
    val on = MaterialTheme.colorScheme.error
    val off = MaterialTheme.colorScheme.outlineVariant
    val levelDesc = stringResource(R.string.rec_mic_level)
    Canvas(modifier.semantics { contentDescription = levelDesc }) {
        val n = 64
        val gap = 2.dp.toPx()
        val w = (size.width - gap * (n - 1)) / n
        val pad = n - levels.size
        for (i in 0 until n) {
            val v = if (i < pad) 0f else levels[i - pad]
            // Speech sits low on a linear scale; a square root spreads it out.
            val h = maxOf(2.dp.toPx(), kotlin.math.sqrt(v.coerceIn(0f, 1f)) * size.height)
            drawRoundRect(if (active) on.copy(alpha = 0.35f + 0.65f * (i.toFloat() / n)) else off,
                topLeft = Offset(i * (w + gap), (size.height - h) / 2), size = Size(w, h), cornerRadius = CornerRadius(w / 2))
        }
    }
}

@Composable
private fun Details(form: RecordingForm, folders: List<SpeakrLabel>, tags: List<SpeakrLabel>, events: List<EventOption>, cb: RecorderCallbacks,
                    expandedAtFirst: Boolean) {
    var open by rememberSaveable { mutableStateOf(expandedAtFirst) }
    var asr by rememberSaveable { mutableStateOf(false) }
    if (!expandedAtFirst) {
        ListItem(headlineContent = { Text(stringResource(R.string.rec_details)) },
            supportingContent = { Text(listOf(form.title, form.event?.title).filterNotNull().filter { it.isNotBlank() }.distinct().joinToString(" · ").ifBlank { stringResource(R.string.rec_details_empty) },
                maxLines = 1, overflow = TextOverflow.Ellipsis) },
            trailingContent = { Icon(if (open) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore, null) },
            modifier = Modifier.clickable(onClickLabel = stringResource(if (open) R.string.rec_hide_details else R.string.rec_show_details)) { open = !open })
    }
    AnimatedVisibility(open) {
        Column(verticalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.padding(vertical = 8.dp)) {
            OutlinedTextField(form.title, { form.title = it }, label = { Text(stringResource(R.string.rec_title_label)) }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                supportingText = { Text(stringResource(R.string.rec_title_sub)) })
            EventPicker(form, events)
            OutlinedTextField(form.participants, { form.participants = it }, label = { Text(stringResource(R.string.rec_participants)) },
                placeholder = { Text("Dana, Evan") }, modifier = Modifier.fillMaxWidth()) // i18n-ignore: sample names
            ListItem(headlineContent = { Text(stringResource(R.string.rec_meeting_date)) },
                supportingContent = { Text(form.meetingDate.format(LocaleFormats.date("EEEMMMdyhmma", "EEE, MMM d, yyyy, h:mm a"))) },
                leadingContent = { Icon(Icons.Outlined.Schedule, null) },
                modifier = Modifier.clickable(onClickLabel = stringResource(R.string.rec_change_date)) { cb.pickDate() })
            if (folders.isNotEmpty()) FolderPicker(form, folders)
            if (tags.isNotEmpty()) TagPicker(form, tags)
            ListItem(headlineContent = { Text(stringResource(R.string.rec_asr_options)) },
                supportingContent = { Text(asrSummary(form)) },
                trailingContent = { Icon(if (asr) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore, null) },
                modifier = Modifier.clickable { asr = !asr })
            AnimatedVisibility(asr) { AsrOptions(form) }
        }
    }
}

private fun asrSummary(f: RecordingForm): String = listOfNotNull(
    f.language?.let { c -> ASR_LANGUAGES.firstOrNull { it.first == c }?.second ?: c } ?: AppText.get(R.string.rec_lang_detected),
    when {
        f.exactSpeakers && f.minSpeakers.isNotBlank() -> AppText.get(R.string.rec_speakers_exact, f.minSpeakers)
        f.minSpeakers.isNotBlank() || f.maxSpeakers.isNotBlank() -> AppText.get(R.string.rec_speakers_range, f.minSpeakers.ifBlank { "1" }, f.maxSpeakers.ifBlank { "…" })
        else -> null
    },
    AppText.get(R.string.rec_hotwords_short).takeIf { f.hotwords.isNotBlank() },
    AppText.get(R.string.rec_prompt_short).takeIf { f.initialPrompt.isNotBlank() },
).joinToString(" · ")

@Composable
private fun EventPicker(form: RecordingForm, events: List<EventOption>) {
    var menu by remember { mutableStateOf(false) }
    val tf = LocaleFormats.date("hmma", "h:mm a")
    Box {
        // The web's select field in its look; a list row in Material's.
        SelectField(stringResource(R.string.rec_event), onClick = { menu = true }, onClickLabel = stringResource(R.string.rec_choose_event),
            leadingIcon = { Icon(Icons.Outlined.CalendarMonth, null) },
            supporting = form.event?.start?.let { s -> { Text(s.format(tf) + (form.event?.end?.let { " – " + it.format(tf) } ?: "")) } }) {
            Text(form.event?.title ?: stringResource(R.string.rec_no_event))
        }
        DropdownMenu(menu, { menu = false }) {
            DropdownMenuItem({ Text(stringResource(R.string.rec_no_event)) }, { menu = false; form.pick(null) })
            events.forEach { e ->
                DropdownMenuItem({ Text("${e.start?.format(tf).orEmpty()}  ${e.title}", maxLines = 1, overflow = TextOverflow.Ellipsis) },
                    { menu = false; form.pick(e) })
            }
        }
    }
}

@Composable
private fun FolderPicker(form: RecordingForm, folders: List<SpeakrLabel>) {
    var menu by remember { mutableStateOf(false) }
    val f = folders.firstOrNull { it.id == form.folderId }
    Box {
        SelectField(stringResource(R.string.rec_folder), onClick = { menu = true }, onClickLabel = stringResource(R.string.rec_choose_folder),
            leadingIcon = { Icon(Icons.Outlined.Folder, null, tint = f?.let { labelColor(it.color, FOLDER_FALLBACK) } ?: LocalContentColor.current) }) {
            Text(f?.name ?: stringResource(R.string.rec_no_folder))
        }
        DropdownMenu(menu, { menu = false }) {
            DropdownMenuItem({ Text(stringResource(R.string.rec_no_folder)) }, { menu = false; form.folderId = null })
            folders.forEach { x ->
                DropdownMenuItem({ Text(x.name) }, { menu = false; form.folderId = x.id },
                    leadingIcon = { Icon(Icons.Outlined.Folder, null, tint = labelColor(x.color, FOLDER_FALLBACK)) })
            }
        }
    }
}

@Composable
private fun TagPicker(form: RecordingForm, tags: List<SpeakrLabel>) {
    Column {
        Text(stringResource(R.string.rec_tags), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            tags.forEach { t ->
                val on = t.id in form.tagIds
                val color = labelColor(t.color, TAG_FALLBACK)
                FilterChip(selected = on, onClick = { form.toggleTag(t) }, label = { Text(t.name) },
                    leadingIcon = { Box(Modifier.size(10.dp).background(color, CircleShape)) })
            }
        }
    }
}

@Composable
private fun AsrOptions(form: RecordingForm) {
    var langMenu by remember { mutableStateOf(false) }
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Box {
            val name = form.language?.let { c -> ASR_LANGUAGES.firstOrNull { it.first == c }?.second ?: c } ?: stringResource(R.string.rec_detect_language)
            // The web's select field in its look (as Event and Folder); Material's button with a menu in Material's.
            if (look.web) SelectField(stringResource(R.string.rec_language_label), onClick = { langMenu = true },
                onClickLabel = stringResource(R.string.rec_choose_language)) { Text(name) }
            else OutlinedButton(onClick = { langMenu = true }, modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.rec_language, name), modifier = Modifier.weight(1f))
                Icon(Icons.Outlined.ExpandMore, null)
            }
            DropdownMenu(langMenu, { langMenu = false }, modifier = Modifier.heightIn(max = 360.dp)) {
                DropdownMenuItem({ Text(stringResource(R.string.rec_detect_language)) }, { langMenu = false; form.language = null })
                ASR_LANGUAGES.forEach { (code, name) -> DropdownMenuItem({ Text(name) }, { langMenu = false; form.language = code }) }
            }
        }
        Text(stringResource(R.string.rec_speakers), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            SegmentedButton(!form.exactSpeakers, { form.exactSpeakers = false }, SegmentedButtonDefaults.itemShape(0, 2)) { Text(stringResource(R.string.rec_range)) }
            SegmentedButton(form.exactSpeakers, { form.exactSpeakers = true }, SegmentedButtonDefaults.itemShape(1, 2)) { Text(stringResource(R.string.rec_exact)) }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            val num = KeyboardOptions(keyboardType = KeyboardType.Number)
            OutlinedTextField(form.minSpeakers, { v -> form.minSpeakers = v.filter(Char::isDigit).take(2) },
                label = { Text(stringResource(if (form.exactSpeakers) R.string.rec_speakers else R.string.rec_min_speakers)) }, singleLine = true, keyboardOptions = num, modifier = Modifier.weight(1f))
            if (!form.exactSpeakers) OutlinedTextField(form.maxSpeakers, { v -> form.maxSpeakers = v.filter(Char::isDigit).take(2) },
                label = { Text(stringResource(R.string.rec_max_speakers)) }, singleLine = true, keyboardOptions = num, modifier = Modifier.weight(1f))
        }
        OutlinedTextField(form.hotwords, { form.hotwords = it }, label = { Text(stringResource(R.string.rec_hotwords)) }, placeholder = { Text(stringResource(R.string.rec_hotwords_hint)) },
            modifier = Modifier.fillMaxWidth())
        OutlinedTextField(form.initialPrompt, { form.initialPrompt = it }, label = { Text(stringResource(R.string.rec_initial_prompt)) }, minLines = 2,
            placeholder = { Text(stringResource(R.string.rec_initial_prompt_hint)) }, modifier = Modifier.fillMaxWidth())
    }
}

/** After Stop: the upload, then a way to the recording sheet. */
@Composable
private fun Finished(s: RecordingSession, progress: Float?, cb: RecorderCallbacks) {
    val cs = MaterialTheme.colorScheme
    Column(Modifier.fillMaxWidth().padding(vertical = 24.dp), horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(s.meta.title.ifBlank { stringResource(R.string.rec_new_title) }, style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { heading() })
        Text("${clock(s.elapsedMs)} · ${estimatedSize(s.elapsedMs)}" + (s.meta.eventTitle?.let { " · $it" } ?: ""),
            style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
        if (s.recovered) Text(stringResource(R.string.rec_recovered),
            style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
        Surface(color = cs.surfaceContainerHigh, shape = MaterialTheme.shapes.large, modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp).semantics { liveRegion = LiveRegionMode.Polite }, verticalArrangement = Arrangement.spacedBy(10.dp)) {
                when (s.state) {
                    RecState.UPLOADED -> {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.CloudDone, null, tint = cs.primary); Spacer(Modifier.width(10.dp))
                            Text(stringResource(R.string.rec_uploaded))
                        }
                        if (s.connectorId != null && s.recordingId != null)
                            Button(onClick = { cb.openRecording(s.connectorId, s.recordingId) }) { Text(stringResource(R.string.rec_open_recording)) }
                    }
                    RecState.FAILED -> {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.ErrorOutline, null, tint = cs.error); Spacer(Modifier.width(10.dp))
                            val failed = s.error ?: stringResource(R.string.rec_upload_failed)
                            Text(if (s.errorFinal) failed else stringResource(R.string.rec_will_retry, failed))
                        }
                        if (s.errorFinal) Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { cb.retry(s.id) }) { Text(stringResource(R.string.rec_try_again)) }
                            TextButton(onClick = { cb.discard(s.id) }) { Text(stringResource(R.string.rec_discard)) }
                        }
                    }
                    else -> {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.CloudUpload, null, tint = cs.primary); Spacer(Modifier.width(10.dp))
                            Text(if (progress != null) stringResource(R.string.rec_uploading_progress, (progress * 100).toInt()) else stringResource(R.string.rec_waiting_connection))
                        }
                        if (progress != null) LinearProgressIndicator(progress = { progress }, modifier = Modifier.fillMaxWidth())
                        else LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                        Text(stringResource(R.string.rec_leave_screen),
                            style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                    }
                }
            }
        }
    }
}

/** A recording still on the phone (waiting, failed), below the form. */
@Composable
private fun WaitingRow(s: RecordingSession, cb: RecorderCallbacks) {
    if (s.state == RecState.UPLOADED) return
    HorizontalDivider()
    ListItem(headlineContent = { Text(s.meta.title.ifBlank { stringResource(R.string.rec_recording) }, maxLines = 1, overflow = TextOverflow.Ellipsis) },
        supportingContent = { Text("${clock(s.elapsedMs)} · " + stringResource(when (s.state) {
            RecState.FAILED -> if (s.errorFinal) R.string.rec_state_failed else R.string.rec_state_retry
            RecState.UPLOADING -> R.string.rec_state_uploading
            else -> R.string.rec_state_waiting
        })) },
        leadingContent = { Icon(if (s.state == RecState.FAILED) Icons.Outlined.ErrorOutline else Icons.Outlined.CloudUpload, null) },
        modifier = Modifier.clickable { cb.select(s.id) })
}
