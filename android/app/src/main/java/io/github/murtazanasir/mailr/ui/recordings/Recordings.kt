package io.github.murtazanasir.mailr.ui.recordings

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import androidx.compose.runtime.Composable
import androidx.compose.runtime.State
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.data.api.EventRef
import io.github.murtazanasir.mailr.data.api.RecordingLink
import io.github.murtazanasir.mailr.data.api.SpeakrApi
import io.github.murtazanasir.mailr.data.api.userMessage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

// Speakr recordings on Android, behind
// Settings → Recordings (Speakr), which is off by default. App.kt provides a [Recordings] through
// [LocalRecordings] only while the switch is on and the server reports a Speakr connector; every
// surface (the event detail, the reader, citation chips) shows nothing when it is null, so with
// the switch off no request goes to /speakr.
//
// The recording sheet (android.recording) is a bottom sheet over any tab: RecordingSheets.kt.

/** The recording the sheet shows, optionally at a moment (seconds). */
data class SheetTarget(val cid: Long, val rid: Long, val t: Double? = null)

/** The action-item review list of one recording. */
data class TasksTarget(val cid: Long, val rid: Long, val title: String, val ev: EventRef?)

/**
 * What the recording surfaces share: the API, the open sheet and review list, and the app's
 * ways out (the browser, compose, the assistant, a linked event, email or task, a snackbar).
 */
class Recordings(
    val api: SpeakrApi,
    private val scope: CoroutineScope,
    val openUrl: (String) -> Unit,
    /** Compose on a draft the server saved (the follow-up). */
    private val openDraft: (Long) -> Unit,
    private val ask: (AskAbout.Recording) -> Unit,
    val openLink: (RecordingLink) -> Unit,
    /** A snackbar: the text, and Undo when there is something to undo. */
    private val notify: (String, (() -> Unit)?) -> Unit,
    /** After tasks were created (the calendar syncs so they show in Tasks). */
    private val tasksAdded: () -> Unit = {},
    /** The New recording screen, for an event or none. */
    private val record: (EventRef?) -> Unit = {},
) {
    var sheet by mutableStateOf<SheetTarget?>(null)
        private set
    var tasks by mutableStateOf<TasksTarget?>(null)
        private set
    /** Which follow-up is being drafted ("summary" or "assistant"), for the buttons' progress. */
    var drafting by mutableStateOf<String?>(null)
        private set
    /** Bumped after a link changes, so open sections load again. */
    var version by mutableIntStateOf(0)
        private set

    fun say(text: String) = notify(text, null)

    private var labelCache: Pair<List<io.github.murtazanasir.mailr.data.api.SpeakrLabel>, List<io.github.murtazanasir.mailr.data.api.SpeakrLabel>>? = null

    /** Whether [labels] has its answer (the screenshot tests wait for the colours). */
    internal val labelsFetched: Boolean get() = labelCache != null

    /** Speakr's folders and tags (for their colours), fetched once per session of the app. */
    suspend fun labels(): Pair<List<io.github.murtazanasir.mailr.data.api.SpeakrLabel>, List<io.github.murtazanasir.mailr.data.api.SpeakrLabel>> =
        labelCache ?: (catching { api.folders() }.getOrDefault(emptyList()) to catching { api.tags() }.getOrDefault(emptyList()))
            .also { labelCache = it }

    // ACT recording.view @android.recording
    fun show(cid: Long, rid: Long, t: Double? = null) {
        sheet = SheetTarget(cid, rid, t)
    }

    fun close() {
        sheet = null
    }

    // ACT recording.to_tasks @android.recording
    fun showTasks(target: TasksTarget?) {
        tasks = target
    }

    // ACT recording.record @android.recording: New recording, prefilled from [ev] (else the current or next event).
    fun newRecording(ev: EventRef?) {
        sheet = null
        record(ev)
    }

    // ACT recording.ask @android.recording: the Assistant tab, a new chat with the recording attached.
    fun askAbout(cid: Long, rid: Long, title: String) {
        sheet = null
        ask(AskAbout.Recording(cid, rid, title))
    }

    // ACT recording.follow_up @android.recording: the server saves a draft; compose opens on it. Nothing is sent.
    fun followUp(cid: Long, rid: Long, mode: String, ev: EventRef?) {
        if (drafting != null) return
        drafting = mode
        scope.launch {
            try {
                val d = api.followUp(cid, rid, mode, ev)
                sheet = null
                openDraft(d.draftId)
                say(AppText.get(if (d.reply) R.string.rec_followup_reply else R.string.rec_followup_drafted))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                say(AppText.get(R.string.rec_followup_failed, e.userMessage()))
            } finally {
                drafting = null
            }
        }
    }

    // ACT recording.link @android.calendar @android.recording: confirm a suggestion, or link an event at that time.
    fun link(cid: Long, rid: Long, kind: String, target: String, occurrence: String?, message: String) {
        scope.launch {
            catching { api.link(cid, rid, kind, target, occurrence) }
                .onSuccess { version++; say(message) }
                .onFailure { if (it is CancellationException) throw it; say(it.userMessage()) }
        }
    }

    // ACT recording.unlink @android.calendar @android.recording: dismiss a suggestion or remove a link (Undo links it again).
    fun unlink(l: RecordingLink, message: String, undo: (() -> Unit)? = null) {
        scope.launch {
            catching { api.unlink(l.id) }
                .onSuccess { version++; notify(message, undo) }
                .onFailure { if (it is CancellationException) throw it; say(it.userMessage()) }
        }
    }

    // ACT recording.to_tasks: the ticked items become tasks linked to the recording.
    suspend fun addTasks(t: TasksTarget, items: List<io.github.murtazanasir.mailr.data.api.ActionItem>): Boolean = try {
        val r = api.createTasks(t.cid, t.rid, items, t.ev)
        val n = r.created.size
        say(if (r.pending > 0) AppText.plural(R.plurals.rec_tasks_added_pending, n, n, r.pending) else AppText.plural(R.plurals.rec_tasks_added, n, n))
        tasks = null
        version++
        tasksAdded()
        true
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        say(AppText.get(R.string.rec_tasks_failed, e.userMessage()))
        false
    }
}

/**
 * Whether recording features show: the switch is on and the server reports a Speakr connector.
 * With the switch off, nothing is asked of the server.
 */
@Composable
fun rememberSpeakrOn(switchOn: Boolean, api: SpeakrApi): Boolean =
    produceState(false, switchOn, api) {
        value = switchOn && try { api.status().connected } catch (e: CancellationException) { throw e } catch (e: Exception) { false }
    }.value

/** Null unless Settings → Recordings (Speakr) is on and the server has a Speakr connector. */
val LocalRecordings = staticCompositionLocalOf<Recordings?> { null }

/** A value a section loads when it is shown. */
sealed interface Load<out T> {
    data object Loading : Load<Nothing>
    data class Failed(val message: String) : Load<Nothing>
    data class Ready<T>(val value: T) : Load<T>
}

@Composable
fun <T> rememberLoad(vararg keys: Any?, block: suspend () -> T): State<Load<T>> =
    produceState<Load<T>>(Load.Loading, *keys) {
        value = try {
            Load.Ready(block())
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Load.Failed(e.userMessage())
        }
    }

/** Seconds as m:ss, or h:mm:ss from an hour on; "" for none. */
fun fmtT(t: Double?): String {
    if (t == null || t.isNaN() || t.isInfinite()) return ""
    val s = maxOf(0L, t.toLong())
    val h = s / 3600
    val m = (s % 3600) / 60
    val sec = (s % 60).toString().padStart(2, '0')
    return if (h > 0) "$h:${m.toString().padStart(2, '0')}:$sec" else "$m:$sec"
}

/** "45 min", "1 h 05 min"; "" for none. */
fun fmtDuration(s: Double?): String {
    if (s == null || s <= 0) return ""
    val m = Math.round(s / 60).toInt()
    return if (m >= 60) AppText.get(R.string.rec_duration_hours, m / 60, (m % 60).toString().padStart(2, '0')) else AppText.get(R.string.rec_duration_minutes, m)
}

/** Speakr's deep link (mailr sends it without ?t=) at a moment: Speakr seeks there and plays. */
fun speakrAt(url: String, t: Double?): String {
    val base = url.replace(Regex("\\?t=\\d+$"), "")
    return if (t != null) "$base?t=${t.toLong()}" else base
}
