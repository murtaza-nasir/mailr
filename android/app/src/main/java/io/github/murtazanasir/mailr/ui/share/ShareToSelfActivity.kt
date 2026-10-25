package io.github.murtazanasir.mailr.ui.share

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.ui.components.followSecurePrefs
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Bundle
import android.view.HapticFeedbackConstants
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.union
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.automirrored.outlined.Label
import androidx.compose.material.icons.automirrored.outlined.Login
import androidx.compose.material.icons.automirrored.outlined.NoteAdd
import androidx.compose.material.icons.automirrored.outlined.Notes
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.automirrored.outlined.Undo
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material.icons.outlined.Language
import androidx.compose.material.icons.outlined.MoreHoriz
import androidx.compose.material.icons.outlined.PictureAsPdf
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import io.github.murtazanasir.mailr.look.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import io.github.murtazanasir.mailr.look.LinearProgressIndicator
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.db.SelfSendEntity
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.share.FileMeta
import io.github.murtazanasir.mailr.data.share.SelfState
import io.github.murtazanasir.mailr.data.share.SelfSubject
import io.github.murtazanasir.mailr.data.share.ShareParser
import io.github.murtazanasir.mailr.data.share.SharedContent
import io.github.murtazanasir.mailr.data.share.TooLargeException
import io.github.murtazanasir.mailr.data.share.UndoResult
import io.github.murtazanasir.mailr.data.share.fileMeta
import io.github.murtazanasir.mailr.ui.MainActivity
import io.github.murtazanasir.mailr.ui.components.LabelChip
import io.github.murtazanasir.mailr.ui.components.LabelPickerSheet
import io.github.murtazanasir.mailr.ui.components.darkSurface
import io.github.murtazanasir.mailr.ui.components.refuseObscuredTouches
import io.github.murtazanasir.mailr.ui.graph
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import io.github.murtazanasir.mailr.ui.util.Format
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import io.github.murtazanasir.mailr.R
import androidx.compose.ui.graphics.asImageBitmap
import io.github.murtazanasir.mailr.i18n.AppText

/**
 * The "Send to self" share target: no composer. A card shows what was shared, with its subject,
 * an optional note and quick labels, and sends it on one tap (Send); after the tap it offers
 * Undo and closes itself, and sending carries on in WorkManager (offline included). Any app can
 * open this card with any content, so nothing is sent without the tap: leaving the card without
 * it sends nothing, and nothing is fetched for it before the tap either.
 */
class ShareToSelfActivity : ComponentActivity() {
    // The app's language before Android 13; a no-op from 13 on.
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(io.github.murtazanasir.mailr.i18n.AppLocales.wrap(newBase))
    }

    private val vm: ShareToSelfViewModel by viewModels {
        viewModelFactory {
            initializer {
                val shared = ShareParser.parse(intent, packageName)
                ShareToSelfViewModel(graph, shared?.takeUnless { it.isEmpty }, contentResolver)
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.auto(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.auto(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT),
        )
        super.onCreate(savedInstanceState)
        followSecurePrefs(graph.settings.prefs)
        val g = graph
        setContent {
            val prefs by g.settings.prefs.collectAsStateWithLifecycle(Prefs())
            val s by vm.state.collectAsStateWithLifecycle()
            MailrTheme(prefs.theme, prefs.dynamicColor, prefs.webColours(), prefs.webLook) {
                // ACT mail.send_to_self @android.share: the card, Send, Undo and the note.
                SelfShareOverlay(s, SelfShareCallbacks(
                    close = ::close,
                    confirm = vm::confirm,
                    undo = vm::undo,
                    addNote = vm::addNote,
                    setNote = vm::setNote,
                    send = vm::sendNote,
                    retry = vm::retry,
                    openApp = {
                        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                        finish()
                    },
                    setSubject = vm::setSubject,
                    toggleLabel = vm::toggleLabel,
                    pickLabels = vm::pickLabels,
                ))
            }
        }
    }

    private fun close() {
        vm.leaving()
        finish()
    }

    override fun finish() {
        super.finish()
        if (Build.VERSION.SDK_INT >= 34) overrideActivityTransition(OVERRIDE_TRANSITION_CLOSE, 0, android.R.anim.fade_out)
        else @Suppress("DEPRECATION") overridePendingTransition(0, android.R.anim.fade_out)
    }
}

enum class SelfPhase { PREPARING, CONFIRM, QUEUED, OFFLINE, SENDING, SENT, EDITING, CANCELLED, FAILED, SIGNED_OUT, NOTHING }

/** A shared link: the address, the title the other app gave (if any) and the site. */
data class SelfLink(val url: String, val title: String?, val domain: String)

/** A shared image: [model] is what the thumbnail loads (a content URI; a painter in tests). */
data class SelfImage(val model: Any, val name: String, val size: Long)

data class SelfFile(val name: String, val type: String, val size: Long)

/** What the card shows of the share, before anything is uploaded. */
data class SelfPreview(
    val text: String? = null,
    val link: SelfLink? = null,
    val images: List<SelfImage> = emptyList(),
    val files: List<SelfFile> = emptyList(),
) {
    val isEmpty: Boolean get() = text == null && link == null && images.isEmpty() && files.isEmpty()

    companion object {
        /**
         * Pure: the shared content and its files' metadata (in the order of [SharedContent.files]);
         * [models] are what the thumbnails load, in the same order.
         */
        fun of(shared: SharedContent, metas: List<FileMeta>, models: List<Any> = emptyList()): SelfPreview {
            val text = shared.text ?: shared.html?.let { Format.htmlToText(it) }
            val url = SelfSubject.firstUrl(text)
            val lone = SelfSubject.isLoneUrl(text)
            val link = url?.let {
                val host = catching { java.net.URI(it).host }.getOrNull()?.removePrefix("www.") ?: it
                SelfLink(it, (shared.subject ?: shared.title).takeIf { lone }, host)
            }
            val images = mutableListOf<SelfImage>()
            val files = mutableListOf<SelfFile>()
            metas.forEachIndexed { i, m ->
                if (m.type.startsWith("image/")) images += SelfImage(models.getOrNull(i) ?: m.name, m.name, m.size)
                else files += SelfFile(m.name, m.type, m.size)
            }
            return SelfPreview(text = text?.trim()?.takeIf { it.isNotEmpty() && !lone }, link = link, images = images, files = files)
        }
    }
}

/** The quick label chips: pure, so the order is unit-tested. */
object SelfLabels {
    const val QUICK = 5

    /** Labels a note can carry: the user's own, never Inbox, Sent, Trash or Spam. */
    fun usable(all: List<Label>): List<Label> = all.filter { it.role == null }

    /**
     * The chips, in a fixed order for the life of the card: the pre-selected labels, then the ones
     * picked most recently on this phone, then the user's most used labels (by message count).
     */
    fun quick(all: List<Label>, recent: List<Long>, selected: Collection<Long>, max: Int = QUICK): List<Label> {
        val user = usable(all)
        val byId = user.associateBy { it.id }
        val out = LinkedHashMap<Long, Label>()
        selected.forEach { id -> byId[id]?.let { out[id] = it } }
        recent.forEach { id -> if (out.size < max) byId[id]?.let { out.putIfAbsent(id, it) } }
        user.sortedWith(compareByDescending<Label> { it.total }.thenBy { it.name.lowercase() })
            .forEach { if (out.size < max) out.putIfAbsent(it.id, it) }
        return out.values.toList()
    }
}

data class SelfUi(
    val phase: SelfPhase = SelfPhase.PREPARING,
    val subject: String = "",
    val files: Int = 0,
    val thumbnail: String? = null,
    val note: String = "",
    val error: String? = null,
    val undoing: Boolean = false,
    val undoGone: Boolean = false,
    /** When the server sends it for real (epoch ms); the card counts down to it. */
    val sendAt: Long? = null,
    val noteOnly: Boolean = false,
    val preview: SelfPreview = SelfPreview(),
    /** What the user typed over the subject; null while untouched (the worker may still improve it). */
    val subjectEdit: String? = null,
    /** Every label the user can pick (the full picker); [quickLabels] are the chips. */
    val labels: List<Label> = emptyList(),
    val quickLabels: List<Label> = emptyList(),
    /** Picked labels, in the order picked. */
    val selected: List<Long> = emptyList(),
    /** The Send tap is on its way to the queue. */
    val submitting: Boolean = false,
) {
    val shownSubject: String get() = subjectEdit ?: subject
    val selectedLabels: List<Label> get() = selected.mapNotNull { id -> labels.firstOrNull { it.id == id } }
}

class ShareToSelfViewModel(
    private val g: AppGraph,
    private val shared: SharedContent?,
    resolver: android.content.ContentResolver,
) : ViewModel() {
    private val _state = MutableStateFlow(SelfUi())
    val state: StateFlow<SelfUi> = _state.asStateFlow()
    private var id: String? = null
    /** The user changed the labels on this card: a late label refresh keeps their choice. */
    private var labelsTouched = false

    init {
        val metas = shared?.files?.map { resolver.fileMeta(it) }.orEmpty()
        val names = metas.map { it.name }
        val image = shared?.files?.firstOrNull { catching { resolver.getType(it) }.getOrNull()?.startsWith("image/") == true }
        _state.value = SelfUi(
            subject = shared?.let { SelfSubject.derive(it.subject, it.title, null, it.text, names) }.orEmpty(),
            files = names.size, thumbnail = image?.toString(), noteOnly = shared == null,
            preview = shared?.let { SelfPreview.of(it, metas, it.files.map { u -> u.toString() }) } ?: SelfPreview(),
        )
        viewModelScope.launch {
            if (g.sessions.session.value == null) {
                _state.update { it.copy(phase = SelfPhase.SIGNED_OUT) }
                return@launch
            }
            launch { loadLabels() }
            try {
                // Opened from the launcher shortcut (no share): a quick note to self.
                val rowId = g.selfSend.create(shared ?: SharedContent(), resolver, startEditing = shared == null)
                id = rowId
                g.selfSend.watch(rowId).collect { row -> row?.let(::apply) }
            } catch (e: TooLargeException) {
                _state.update { it.copy(phase = SelfPhase.FAILED, error = AppText.get(R.string.sharing_too_large, e.name, AppText.get(R.string.sharing_size_limit))) }
            } catch (e: Exception) {
                _state.update { it.copy(phase = SelfPhase.FAILED, error = e.message ?: AppText.get(R.string.sharing_couldnt_read)) }
            }
        }
    }

    /**
     * The user's labels (the offline copy when the server cannot be reached), with the labels of
     * the last Send to self from this phone picked again.
     */
    private suspend fun loadLabels() {
        val prefs = g.settings.prefs.first()
        val known = g.mail.labels.value
        if (known.isNotEmpty()) showLabels(known, prefs)
        val all = catching { g.mail.refreshLabels() }.getOrNull().orEmpty()
        if (all.isNotEmpty()) showLabels(all, prefs)
    }

    private fun showLabels(all: List<Label>, prefs: Prefs) = _state.update { s ->
        val user = SelfLabels.usable(all)
        val ids = user.map { it.id }.toSet()
        // Only labels that still exist are picked: one deleted since is dropped, never sent unseen.
        val selected = (if (labelsTouched) s.selected else prefs.selfSendLabels).filter { it in ids }
        s.copy(labels = user, selected = selected,
            quickLabels = if (labelsTouched && s.quickLabels.isNotEmpty()) s.quickLabels
            else SelfLabels.quick(user, prefs.selfSendRecentLabels, selected))
    }

    private fun apply(row: SelfSendEntity) {
        val phase = when (row.state) {
            SelfState.CONFIRM -> SelfPhase.CONFIRM
            SelfState.QUEUED -> if (online()) SelfPhase.QUEUED else SelfPhase.OFFLINE
            SelfState.SENDING -> SelfPhase.SENDING
            SelfState.HELD -> SelfPhase.SENT
            SelfState.EDITING -> SelfPhase.EDITING
            SelfState.CANCELLED -> SelfPhase.CANCELLED
            SelfState.FAILED -> SelfPhase.FAILED
            else -> SelfPhase.NOTHING
        }
        _state.update { s ->
            s.copy(phase = if (s.undoing && phase != SelfPhase.CANCELLED && phase != SelfPhase.EDITING) s.phase else phase,
                subject = row.subject.ifBlank { s.subject }, error = row.error, sendAt = row.sendAt,
                // The note the row carries (a card opened again keeps what was typed).
                note = s.note.ifBlank { row.note },
                submitting = s.submitting && phase == SelfPhase.CONFIRM)
        }
    }

    private fun online(): Boolean {
        val cm = g.context.getSystemService(ConnectivityManager::class.java) ?: return true
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
    }

    fun setSubject(v: String) = _state.update { it.copy(subjectEdit = v) }

    fun toggleLabel(labelId: Long) {
        labelsTouched = true
        _state.update { s -> s.copy(selected = if (labelId in s.selected) s.selected - labelId else s.selected + labelId) }
    }

    /** From the full picker ("More…"): the new choice. A label picked there joins the chips, first. */
    fun pickLabels(ids: List<Long>) {
        labelsTouched = true
        _state.update { s ->
            val known = s.labels.associateBy { it.id }
            val extra = ids.filter { id -> s.quickLabels.none { it.id == id } }.mapNotNull { known[it] }
            s.copy(selected = ids.filter { it in known }.distinct(), quickLabels = extra + s.quickLabels)
        }
    }

    /** The user's Send tap on a share: only now is it queued, with the card's subject, note and labels. */
    fun confirm() {
        val rowId = id ?: return
        val s = _state.value
        if (s.phase != SelfPhase.CONFIRM || s.submitting) return
        _state.update { it.copy(submitting = true) }
        val labels = s.selected
        g.scope.launch {
            g.settings.rememberSelfSendLabels(labels)
            g.selfSend.confirm(rowId, subject = s.subjectEdit.orEmpty(), note = s.note, labelIds = labels)
        }
    }

    fun undo() {
        val rowId = id ?: return
        _state.update { it.copy(undoing = true) }
        viewModelScope.launch {
            val r = g.selfSend.cancel(rowId)
            _state.update {
                when (r) {
                    UndoResult.STOPPED -> it.copy(undoing = false, phase = SelfPhase.CANCELLED)
                    UndoResult.TOO_LATE -> it.copy(undoing = false, undoGone = true, phase = SelfPhase.SENT)
                    UndoResult.OFFLINE -> it.copy(undoing = false, error = AppText.get(R.string.sharing_couldnt_reach_undo))
                    UndoResult.GONE -> it.copy(undoing = false, undoGone = true)
                }
            }
        }
    }

    fun addNote() {
        val rowId = id ?: return
        _state.update { it.copy(undoing = true) }
        viewModelScope.launch {
            val r = g.selfSend.pauseForNote(rowId)
            _state.update {
                if (r == UndoResult.STOPPED) it.copy(undoing = false, phase = SelfPhase.EDITING)
                else it.copy(undoing = false, undoGone = true, error = if (r == UndoResult.TOO_LATE) AppText.get(R.string.sharing_already_sent) else it.error)
            }
        }
    }

    fun setNote(v: String) = _state.update { it.copy(note = v) }

    fun sendNote() {
        val rowId = id ?: return
        val s = _state.value
        if (s.noteOnly && s.note.isBlank()) return
        val labels = s.selected
        g.scope.launch {
            g.settings.rememberSelfSendLabels(labels)
            g.selfSend.sendWithNote(rowId, s.note, labels)
        }
    }

    fun retry() {
        val rowId = id ?: return
        g.scope.launch { g.selfSend.retry(rowId) }
    }

    /**
     * The card is going away. A share never confirmed is dropped; a note being written (the
     * user tapped "Add a note") still gets sent, so nothing typed is lost.
     */
    fun leaving() {
        val rowId = id ?: return
        val s = _state.value
        if (s.phase == SelfPhase.CONFIRM && !s.submitting) {
            g.scope.launch { g.selfSend.cancel(rowId) }
            return
        }
        if (s.phase == SelfPhase.EDITING) {
            if (s.noteOnly && s.note.isBlank()) g.scope.launch { g.selfSend.cancel(rowId) }
            else g.scope.launch { g.selfSend.sendWithNote(rowId, s.note, s.selected) }
        }
    }

    override fun onCleared() {
        leaving()
    }
}

data class SelfShareCallbacks(
    val close: () -> Unit,
    /** Send on the confirmation card. */
    val confirm: () -> Unit,
    val undo: () -> Unit,
    val addNote: () -> Unit,
    val setNote: (String) -> Unit,
    val send: () -> Unit,
    val retry: () -> Unit,
    val openApp: () -> Unit,
    val setSubject: (String) -> Unit = {},
    val toggleLabel: (Long) -> Unit = {},
    /** The full picker's choice (every picked id). */
    val pickLabels: (List<Long>) -> Unit = {},
)

/** The card shows its form (what is sent, subject, note, labels) in these phases. */
private fun SelfUi.form(): Boolean = phase == SelfPhase.CONFIRM || phase == SelfPhase.EDITING ||
    (phase == SelfPhase.PREPARING && !noteOnly)

@Composable
fun SelfShareOverlay(s: SelfUi, cb: SelfShareCallbacks) {
    io.github.murtazanasir.mailr.ui.components.HideOverlays()
    val view = LocalView.current
    var shown by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { shown = true }
    val scrim by animateFloatAsState(if (shown) 0.32f else 0f, tween(220), label = "scrim") // i18n-ignore: animation label
    val editing = s.phase == SelfPhase.EDITING
    val leave = { if (editing && !(s.noteOnly && s.note.isBlank())) cb.send() else cb.close() }

    BackHandler { leave() }

    // The card closes by itself once there is nothing left to decide.
    val linger = when (s.phase) {
        SelfPhase.SENT -> 4_500L
        SelfPhase.OFFLINE -> 3_500L
        SelfPhase.CANCELLED, SelfPhase.NOTHING -> 1_400L
        else -> null
    }
    val countdown = remember { Animatable(1f) }
    LaunchedEffect(s.phase, s.undoing) {
        if (linger == null || s.undoing) {
            countdown.snapTo(1f)
            return@LaunchedEffect
        }
        if (s.phase == SelfPhase.SENT && Build.VERSION.SDK_INT >= 30) view.performHapticFeedback(HapticFeedbackConstants.CONFIRM)
        countdown.snapTo(1f)
        countdown.animateTo(0f, tween(linger.toInt(), easing = LinearEasing))
        cb.close()
    }

    Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = scrim))
        .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null,
            onClickLabel = stringResource(R.string.sharing_close)) { leave() }) {
        AnimatedVisibility(
            visible = shown,
            enter = slideInVertically(tween(260)) { it / 2 } + fadeIn(tween(200)),
            modifier = Modifier.align(Alignment.BottomCenter)
                // The keyboard never covers Send: the card sits above it, the navigation bar and below the status bar.
                .windowInsetsPadding(WindowInsets.navigationBars.union(WindowInsets.ime).union(WindowInsets.statusBars))
                .padding(12.dp),
        ) {
            Surface(
                shape = RoundedCornerShape(28.dp),
                color = MaterialTheme.colorScheme.surfaceContainerHigh,
                tonalElevation = 3.dp, shadowElevation = 6.dp,
                modifier = Modifier.widthIn(max = 560.dp).fillMaxWidth()
                    // Taps on the card do not reach the scrim.
                    .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) {},
            ) {
                if (s.form()) FormCard(s, cb) else StatusCard(s, cb, linger, countdown.value)
            }
        }
    }
}

// ---------------- the form: what is sent, subject, note, labels, Send ----------------

@Composable
private fun FormCard(s: SelfUi, cb: SelfShareCallbacks) {
    var picker by remember { mutableStateOf(false) }
    val editing = s.phase == SelfPhase.EDITING
    Column {
        // The header and the buttons stay put; the middle scrolls on a short screen or over the keyboard.
        Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp, top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Surface(shape = CircleShape, color = MaterialTheme.colorScheme.primaryContainer, modifier = Modifier.size(36.dp)) {
                Box(contentAlignment = Alignment.Center) {
                    Icon(if (s.noteOnly || editing) Icons.AutoMirrored.Outlined.NoteAdd else Icons.AutoMirrored.Outlined.Send, null,
                        Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onPrimaryContainer)
                }
            }
            Spacer(Modifier.width(12.dp))
            Text(formTitle(s), style = MaterialTheme.typography.titleLarge, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f).semantics { heading(); liveRegion = LiveRegionMode.Polite })
            IconButton(onClick = cb.close, modifier = Modifier.testTag("self-send-close")) {
                Icon(Icons.Outlined.Close, contentDescription = if (editing && !s.noteOnly) stringResource(R.string.sharing_close) else stringResource(R.string.sharing_cancel))
            }
        }
        Column(
            Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp).padding(top = 4.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (!s.noteOnly && !s.preview.isEmpty) Preview(s.preview)
            if (!s.noteOnly && !editing) SubjectField(s, cb)
            NoteField(s, cb, focusFirst = editing)
            LabelRow(s, cb, onMore = { picker = true })
        }
        FormActions(s, cb)
    }
    if (picker) {
        LabelPickerSheet(s.labels, s.selected.toSet(), title = stringResource(R.string.sharing_labels_for_note), onDismiss = { picker = false }) { add, remove ->
            cb.pickLabels(s.selected.filter { it !in remove } + add.filter { it !in s.selected })
            picker = false
        }
    }
}

private fun formTitle(s: SelfUi): String = AppText.get(when {
    s.noteOnly -> R.string.sharing_note_to_self
    s.phase == SelfPhase.EDITING -> R.string.sharing_add_note
    else -> R.string.sharing_send_to_yourself
})

@Composable
private fun Preview(p: SelfPreview) {
    val shape = RoundedCornerShape(16.dp)
    // Lighter than the card in the light theme, a step lighter again in the dark one (never a black hole).
    val bg = if (darkSurface()) MaterialTheme.colorScheme.surfaceContainerHighest else MaterialTheme.colorScheme.surfaceContainerLowest
    Surface(shape = shape, color = bg,
        modifier = Modifier.fillMaxWidth().border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape).testTag("self-send-preview")) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            p.link?.let { LinkPreview(it) }
            p.text?.let { t ->
                Row(verticalAlignment = Alignment.Top, modifier = Modifier.semantics(mergeDescendants = true) {}) {
                    Icon(Icons.AutoMirrored.Outlined.Notes, null, Modifier.padding(top = 2.dp).size(18.dp),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.width(10.dp))
                    Text(t, style = MaterialTheme.typography.bodyMedium, maxLines = 4, overflow = TextOverflow.Ellipsis)
                }
            }
            if (p.images.isNotEmpty()) ImageStrip(p.images)
            p.files.forEach { FileRow(it) }
        }
    }
}

@Composable
private fun LinkPreview(l: SelfLink) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.semantics(mergeDescendants = true) {}) {
        Surface(shape = RoundedCornerShape(10.dp), color = MaterialTheme.colorScheme.secondaryContainer, modifier = Modifier.size(40.dp)) {
            Box(contentAlignment = Alignment.Center) {
                Icon(Icons.Outlined.Language, null, Modifier.size(22.dp), tint = MaterialTheme.colorScheme.onSecondaryContainer)
            }
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(l.title ?: SelfSubject.shortUrl(l.url), style = MaterialTheme.typography.titleSmall, maxLines = 2,
                overflow = TextOverflow.Ellipsis)
            Text(l.domain, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun ImageStrip(images: List<SelfImage>) {
    val shown = images.take(4)
    val more = images.size - shown.size
    val one = images.size == 1
    val desc = if (one) stringResource(R.string.sharing_image_named, images[0].name)
        else pluralStringResource(R.plurals.sharing_images_n, images.size, images.size)
    Row(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {
        contentDescription = desc
    }, horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        shown.forEachIndexed { i, img ->
            Box(Modifier.size(if (one) 88.dp else 68.dp).clip(RoundedCornerShape(12.dp))
                .background(MaterialTheme.colorScheme.surfaceVariant), contentAlignment = Alignment.Center) {
                Icon(Icons.Outlined.Image, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                when (val m = img.model) {
                    is Painter -> Image(m, null, contentScale = ContentScale.Crop, modifier = Modifier.fillMaxSize())
                    else -> UriThumbnail(m, Modifier.fillMaxSize())
                }
                if (i == shown.lastIndex && more > 0) {
                    Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.55f)), contentAlignment = Alignment.Center) {
                        Text(stringResource(R.string.sharing_more_count, more), color = Color.White, style = MaterialTheme.typography.titleMedium)
                    }
                }
            }
        }
        if (one) {
            Column(Modifier.weight(1f)) {
                Text(images[0].name, style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                if (images[0].size > 0) Text(Format.bytes(images[0].size), style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun FileRow(f: SelfFile) {
    val ext = f.name.substringAfterLast('.', "").lowercase()
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}) {
        Surface(shape = RoundedCornerShape(10.dp), color = MaterialTheme.colorScheme.tertiaryContainer, modifier = Modifier.size(40.dp)) {
            Box(contentAlignment = Alignment.Center) {
                Icon(if (f.type == "application/pdf" || ext == "pdf") Icons.Outlined.PictureAsPdf else Icons.Outlined.Description,
                    null, Modifier.size(22.dp), tint = MaterialTheme.colorScheme.onTertiaryContainer)
            }
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(f.name, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (f.size > 0) Text(Format.bytes(f.size), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun SubjectField(s: SelfUi, cb: SelfShareCallbacks) {
    // A bare link's subject is its address until the worker reads the page title (after Send).
    val link = s.preview.link
    val pageTitleLater = s.subjectEdit == null && link != null && link.title == null && s.preview.text == null
    OutlinedTextField(
        s.shownSubject, cb.setSubject,
        label = { Text(stringResource(R.string.sharing_subject)) },
        singleLine = true,
        supportingText = if (pageTitleLater) ({ Text(stringResource(R.string.sharing_page_title_later)) }) else null,
        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Next),
        modifier = Modifier.fillMaxWidth().testTag("self-send-subject"),
    )
}

@Composable
private fun NoteField(s: SelfUi, cb: SelfShareCallbacks, focusFirst: Boolean) {
    val focus = remember { FocusRequester() }
    if (focusFirst) LaunchedEffect(Unit) { catching { focus.requestFocus() } }
    OutlinedTextField(
        s.note, cb.setNote,
        label = { Text(if (s.noteOnly) stringResource(R.string.sharing_note) else stringResource(R.string.sharing_note_optional)) },
        placeholder = { Text(if (s.noteOnly) stringResource(R.string.sharing_note_placeholder) else stringResource(R.string.sharing_note_why)) },
        minLines = if (s.noteOnly) 3 else 1, maxLines = 5,
        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
        modifier = Modifier.fillMaxWidth().focusRequester(focus).testTag("self-send-note"),
    )
}

/** ACT mail.send_to_self_labels @android.share: quick label chips; "More…" opens the full picker. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun LabelRow(s: SelfUi, cb: SelfShareCallbacks, onMore: () -> Unit) {
    val moreLabels = stringResource(R.string.sharing_more_labels)
    Column(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(stringResource(R.string.sharing_labels), style = MaterialTheme.typography.titleSmall, modifier = Modifier.semantics { heading() })
            Spacer(Modifier.width(8.dp))
            Text(when (s.selected.size) {
                0 -> stringResource(R.string.sharing_filters_file_it)
                else -> pluralStringResource(R.plurals.sharing_picked_n, s.selected.size, s.selected.size)
            }, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        // The chips wrap, so "More…" is always in sight (a sideways row hid it behind the edge).
        FlowRow(Modifier.fillMaxWidth().testTag("self-send-labels"), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            s.quickLabels.forEach { lb -> LabelFilterChip(lb, lb.id in s.selected) { cb.toggleLabel(lb.id) } }
            FilterChip(
                selected = false, onClick = onMore,
                label = { Text(if (s.quickLabels.isEmpty()) stringResource(R.string.sharing_pick_labels) else stringResource(R.string.sharing_more)) },
                leadingIcon = { Icon(Icons.Outlined.MoreHoriz, null, Modifier.size(FilterChipDefaults.IconSize)) },
                modifier = Modifier.testTag("self-send-more-labels").semantics { contentDescription = moreLabels },
            )
        }
    }
}

@Composable
private fun LabelFilterChip(lb: Label, selected: Boolean, onClick: () -> Unit) {
    val dark = darkSurface()
    val pair = Format.labelColors(lb.color, dark)
    val dot = Format.labelDot(lb.color, dark) ?: MaterialTheme.colorScheme.outline
    val labelDesc = stringResource(R.string.sharing_label_named, lb.name)
    val pickedDesc = stringResource(if (selected) R.string.sharing_picked else R.string.sharing_not_picked)
    FilterChip(
        selected = selected, onClick = onClick,
        label = { Text(lb.leaf, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 160.dp)) },
        leadingIcon = {
            if (selected) Icon(Icons.Outlined.Check, null, Modifier.size(FilterChipDefaults.IconSize))
            else Icon(Icons.AutoMirrored.Outlined.Label, null, Modifier.size(FilterChipDefaults.IconSize), tint = dot)
        },
        colors = FilterChipDefaults.filterChipColors(
            selectedContainerColor = pair?.first ?: MaterialTheme.colorScheme.secondaryContainer,
            selectedLabelColor = pair?.second ?: MaterialTheme.colorScheme.onSecondaryContainer,
            selectedLeadingIconColor = pair?.second ?: MaterialTheme.colorScheme.onSecondaryContainer,
        ),
        border = FilterChipDefaults.filterChipBorder(enabled = true, selected = selected,
            selectedBorderColor = (pair?.second ?: MaterialTheme.colorScheme.onSecondaryContainer).copy(alpha = 0.3f),
            selectedBorderWidth = 1.dp),
        modifier = Modifier.testTag("self-label-${lb.id}").semantics {
            contentDescription = labelDesc
            stateDescription = pickedDesc
        },
    )
}

@Composable
private fun FormActions(s: SelfUi, cb: SelfShareCallbacks) {
    val editing = s.phase == SelfPhase.EDITING
    val busy = s.phase == SelfPhase.PREPARING || s.submitting
    val ready = if (editing) !(s.noteOnly && s.note.isBlank()) else !busy
    Row(Modifier.fillMaxWidth().padding(start = 12.dp, end = 20.dp, top = 8.dp, bottom = 16.dp),
        horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.CenterVertically) {
        if (editing && !s.noteOnly) TextButton(onClick = { cb.setNote(""); cb.send() }) { Text(stringResource(R.string.sharing_send_without_note)) }
        else TextButton(onClick = cb.close, modifier = Modifier.testTag("self-send-cancel")) { Text(stringResource(R.string.sharing_cancel)) }
        Spacer(Modifier.width(8.dp))
        Button(
            onClick = if (editing) cb.send else cb.confirm, enabled = ready,
            modifier = Modifier.heightIn(min = 48.dp).widthIn(min = 112.dp).refuseObscuredTouches().testTag("self-send-confirm"),
        ) {
            if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = LocalContentColor.current)
            else Icon(Icons.AutoMirrored.Filled.Send, null, Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text(when {
                s.phase == SelfPhase.PREPARING -> stringResource(R.string.sharing_preparing)
                s.submitting -> stringResource(R.string.sharing_sending)
                else -> stringResource(R.string.sharing_send)
            }, fontWeight = FontWeight.Medium)
        }
    }
}

// ---------------- after Send: status, Undo, the countdown ----------------

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun StatusCard(s: SelfUi, cb: SelfShareCallbacks, linger: Long?, progress: Float) {
    Column {
        Row(Modifier.padding(start = 16.dp, end = 12.dp, top = 16.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            StatusBadge(s.phase)
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(title(s), style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                val sub = subtitle(s)
                if (sub.isNotEmpty()) Text(sub, style = MaterialTheme.typography.bodyMedium, maxLines = 2,
                    overflow = TextOverflow.Ellipsis, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            s.thumbnail?.let { uri ->
                Spacer(Modifier.width(12.dp))
                val img = s.preview.images.firstOrNull()?.model
                if (img is Painter) Image(img, null, contentScale = ContentScale.Crop, modifier = Modifier.size(48.dp).clip(RoundedCornerShape(10.dp)))
                else UriThumbnail(uri, Modifier.size(48.dp).clip(RoundedCornerShape(10.dp)))
            }
        }
        val sentPhases = setOf(SelfPhase.QUEUED, SelfPhase.OFFLINE, SelfPhase.SENDING, SelfPhase.SENT)
        // The note typed on the form went with the message: the card shows it.
        if (s.note.isNotBlank() && s.phase in sentPhases) {
            Text(stringResource(R.string.sharing_sent_note, s.note.trim()), style = MaterialTheme.typography.bodyMedium,
                maxLines = 2, overflow = TextOverflow.Ellipsis, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 70.dp, end = 16.dp, top = 2.dp).testTag("self-send-sent-note"))
        }
        val picked = s.selectedLabels
        val pickedList = stringResource(R.string.sharing_labels_list, picked.joinToString(", ") { it.leaf })
        if (picked.isNotEmpty() && s.phase in sentPhases) {
            FlowRow(Modifier.padding(start = 70.dp, end = 16.dp, top = 2.dp).semantics(mergeDescendants = true) {
                contentDescription = pickedList
            }, horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                picked.forEach { LabelChip(it) }
            }
        }
        Actions(s, cb)
        if (linger != null && !s.undoing) {
            LinearProgressIndicator(progress = { progress }, modifier = Modifier.fillMaxWidth().height(3.dp),
                trackColor = Color.Transparent, drawStopIndicator = {})
        }
    }
}

@Composable
private fun StatusBadge(phase: SelfPhase) {
    Surface(shape = CircleShape, color = when (phase) {
        SelfPhase.FAILED -> MaterialTheme.colorScheme.errorContainer
        SelfPhase.SENT -> MaterialTheme.colorScheme.primary
        else -> MaterialTheme.colorScheme.secondaryContainer
    }, modifier = Modifier.size(40.dp)) {
        Box(contentAlignment = Alignment.Center) {
            AnimatedContent(phase, transitionSpec = { (fadeIn() + scaleIn(initialScale = 0.6f)) togetherWith fadeOut() }, label = "badge") { p -> // i18n-ignore: animation label
                when (p) {
                    SelfPhase.PREPARING, SelfPhase.QUEUED, SelfPhase.SENDING ->
                        CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.5.dp)
                    SelfPhase.SENT -> Icon(Icons.Outlined.Check, null, tint = MaterialTheme.colorScheme.onPrimary)
                    SelfPhase.OFFLINE -> Icon(Icons.Outlined.CloudOff, null)
                    SelfPhase.CANCELLED -> Icon(Icons.AutoMirrored.Outlined.Undo, null)
                    SelfPhase.FAILED -> Icon(Icons.Outlined.ErrorOutline, null, tint = MaterialTheme.colorScheme.onErrorContainer)
                    SelfPhase.SIGNED_OUT -> Icon(Icons.AutoMirrored.Outlined.Login, null)
                    SelfPhase.EDITING, SelfPhase.NOTHING -> Icon(Icons.AutoMirrored.Outlined.NoteAdd, null)
                    SelfPhase.CONFIRM -> Icon(Icons.AutoMirrored.Outlined.Send, null)
                }
            }
        }
    }
}

private fun title(s: SelfUi): String = AppText.get(when (s.phase) {
    SelfPhase.PREPARING -> R.string.sharing_getting_ready
    SelfPhase.CONFIRM -> R.string.sharing_send_to_yourself
    SelfPhase.QUEUED, SelfPhase.SENDING -> R.string.sharing_sending_to_yourself
    SelfPhase.SENT -> R.string.sharing_sent_to_yourself
    SelfPhase.OFFLINE -> R.string.sharing_will_send_online
    SelfPhase.EDITING -> if (s.noteOnly) R.string.sharing_note_to_self else R.string.sharing_add_note
    SelfPhase.CANCELLED -> R.string.sharing_not_sent
    SelfPhase.FAILED -> R.string.sharing_couldnt_send
    SelfPhase.SIGNED_OUT -> R.string.sharing_sign_in_first
    SelfPhase.NOTHING -> R.string.sharing_nothing_to_send
})

private fun subtitle(s: SelfUi): String = when (s.phase) {
    SelfPhase.FAILED -> s.error ?: s.shownSubject
    SelfPhase.SIGNED_OUT -> AppText.get(R.string.sharing_then_share_again)
    else -> listOfNotNull(s.shownSubject.ifBlank { null }, when (s.files) {
        0 -> null
        else -> AppText.plural(R.plurals.sharing_files_n, s.files, s.files)
    }).joinToString(AppText.get(R.string.sharing_subtitle_sep))
}

@Composable
private fun Actions(s: SelfUi, cb: SelfShareCallbacks) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 4.dp), horizontalArrangement = Arrangement.End,
        verticalAlignment = Alignment.CenterVertically) {
        when (s.phase) {
            SelfPhase.FAILED -> {
                TextButton(onClick = cb.close) { Text(stringResource(R.string.sharing_close)) }
                if (s.error?.contains(stringResource(R.string.sharing_size_limit)) != true) TextButton(onClick = cb.retry) { Text(stringResource(R.string.sharing_try_again)) }
            }
            SelfPhase.SIGNED_OUT -> {
                TextButton(onClick = cb.close) { Text(stringResource(R.string.sharing_close)) }
                TextButton(onClick = cb.openApp) { Text(stringResource(R.string.sharing_open_mailr)) }
            }
            SelfPhase.CANCELLED, SelfPhase.NOTHING, SelfPhase.PREPARING -> Spacer(Modifier.height(40.dp))
            else -> {
                val busy = s.undoing
                if (!s.undoGone) {
                    // Only when no note went with it: a note typed on the form is never asked for again.
                    if (s.note.isBlank()) TextButton(onClick = cb.addNote, enabled = !busy, modifier = Modifier.testTag("self-send-add-note")) {
                        Icon(Icons.AutoMirrored.Outlined.NoteAdd, null, Modifier.size(18.dp))
                        Spacer(Modifier.width(6.dp))
                        Text(stringResource(R.string.sharing_add_note))
                    }
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = cb.undo, enabled = !busy, modifier = Modifier.testTag("self-send-undo")) {
                        Text(if (busy) stringResource(R.string.sharing_undoing) else stringResource(R.string.sharing_undo))
                    }
                } else {
                    Text(stringResource(R.string.sharing_too_late_undo), style = MaterialTheme.typography.labelLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(12.dp))
                }
            }
        }
    }
}

/**
 * A shared image's thumbnail, read through the content resolver (ContentResolver.loadThumbnail, at
 * most 256 px) off the main thread. Nothing shows while it loads or when the image cannot be read.
 */
@Composable
private fun UriThumbnail(model: Any?, modifier: Modifier) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val uri = when (model) {
        is android.net.Uri -> model
        is String -> catching { android.net.Uri.parse(model) }.getOrNull()
        else -> null
    }
    val bmp by androidx.compose.runtime.produceState<androidx.compose.ui.graphics.ImageBitmap?>(null, uri) {
        if (uri == null) return@produceState
        value = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            try {
                ctx.contentResolver.loadThumbnail(uri, android.util.Size(256, 256), null)
                    .asImageBitmap()
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                null
            }
        }
    }
    bmp?.let { Image(it, null, contentScale = ContentScale.Crop, modifier = modifier) }
}
