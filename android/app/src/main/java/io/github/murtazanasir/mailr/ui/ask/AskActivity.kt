package io.github.murtazanasir.mailr.ui.ask

import io.github.murtazanasir.mailr.util.catching
import io.github.murtazanasir.mailr.data.voice.DictationContext

import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.ui.components.followSecurePrefs
import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.layout.union
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Lightbulb
import androidx.compose.material.icons.outlined.Dns
import androidx.compose.material.icons.outlined.Event
import androidx.compose.material.icons.outlined.GraphicEq
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.Mail
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material.icons.outlined.Stop
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material.icons.outlined.VerifiedUser
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.FilledTonalIconButton
import io.github.murtazanasir.mailr.look.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import io.github.murtazanasir.mailr.look.InputChip
import io.github.murtazanasir.mailr.look.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.data.api.AssistantGate
import io.github.murtazanasir.mailr.data.voice.VoiceSource
import io.github.murtazanasir.mailr.data.voice.VoiceState
import io.github.murtazanasir.mailr.ui.graph
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/**
 * The ask capture (android.ask): a compact card to say or type an instruction for the assistant,
 * with the item it was opened from attached. Opened by the "Ask mailr" widget, the launcher
 * shortcut, the Quick Settings tile, a reminder's Ask button and the Ask buttons in the app.
 * Send hands the words to the Assistant tab, where the reply streams and approvals ask as usual.
 * It never shows over the lock screen. While the assistant is off for the account (GET /me
 * `access`), the card shows the server's message and offers nothing to send.
 *
 * ACT agent.dictate @android.ask, ACT agent.ask_about @android.ask
 */
class AskActivity : ComponentActivity() {
    // The app's language before Android 13; a no-op from 13 on.
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(io.github.murtazanasir.mailr.i18n.AppLocales.wrap(newBase))
    }

    private lateinit var model: AskCaptureModel

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.auto(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.auto(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT),
        )
        super.onCreate(savedInstanceState)
        followSecurePrefs(graph.settings.prefs)
        // The window's title (TalkBack, the task switcher) with the user's name; the manifest has the default.
        title = getString(R.string.askui_title, io.github.murtazanasir.mailr.i18n.AssistantName.now())
        val g = graph
        // The settings are local (DataStore) and small: read off the main thread, then the capture opens.
        lifecycleScope.launch { start(g, g.settings.prefs.first()) }
    }

    private fun start(g: io.github.murtazanasir.mailr.AppGraph, prefs: io.github.murtazanasir.mailr.data.settings.Prefs) {
        val handsFree = prefs.dictationHandsFree
        val voice = VoiceSetup(this, g, lifecycleScope, handsFree, g.mail.me.value?.features?.transcribe ?: prefs.serverDictation)
        voice.clipContext = { DictationContext.of(null, if (::model.isInitialized) model.ui.value.about else null) }
        // Whether the assistant is on: the access the app saw last, until the server answers.
        val blocked = MutableStateFlow(AssistantGate.blocked(g.mail.me.value, prefs.assistantBlocked))
        model = AskCaptureModel(AskIntents.read(intent), voice.source(prefs.dictationOrder), handsFree, voice::engines,
            lifecycleScope, fallback = voice::fallback) handOff@{ req ->
            if (blocked.value != null) return@handOff
            g.pendingAsk.value = req
            startActivity(AskIntents.openAssistant(this))
            finish()
        }
        // Whether the server can transcribe, when the app has not asked yet this run.
        if (g.mail.me.value == null && g.sessions.session.value != null) lifecycleScope.launch {
            val me = catching { g.mail.refreshMe() }.getOrNull() ?: return@launch
            val off = AssistantGate.blocked(me)
            blocked.value = off
            if (off.orEmpty() != prefs.assistantBlocked) g.scope.launch { g.settings.update { it.copy(assistantBlocked = off.orEmpty()) } }
            val known = me.features.transcribe
            if (known != voice.server) {
                voice.server = known
                g.scope.launch { g.settings.update { it.copy(serverDictation = known) } }
                model.setSource(voice.source(prefs.dictationOrder))
            }
        }
        val listenAtOnce = AskIntents.listen(intent) ?: prefs.dictationListen
        setContent {
            val p by g.settings.prefs.collectAsStateWithLifecycle(prefs)
            val ui by model.ui.collectAsStateWithLifecycle()
            val off by blocked.collectAsStateWithLifecycle()
            val mic = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok -> if (ok) model.listen() }
            val listen = {
                if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) model.listen()
                else mic.launch(Manifest.permission.RECORD_AUDIO)
            }
            LaunchedEffect(off == null) { if (off == null && listenAtOnce && ui.source != VoiceSource.NONE) listen() else if (off != null) model.cancelListening() }
            MailrTheme(p.theme, p.dynamicColor, p.webColours(), p.webLook) {
                AskOverlay(ui, AskCallbacks(
                    setText = model::setText, listen = listen, stop = model::stopListening, send = model::send,
                    openChat = model::openChat, clearAbout = model::clearAbout,
                    setHandsFree = { v ->
                        model.setHandsFree(v)
                        g.scope.launch { g.settings.update { it.copy(dictationHandsFree = v) } }
                    },
                    download = { model.download() }, close = ::finish,
                ), focusText = !listenAtOnce || ui.source == VoiceSource.NONE, blocked = off)
            }
        }
    }

    override fun onDestroy() {
        if (::model.isInitialized) model.close()
        super.onDestroy()
    }

    override fun finish() {
        super.finish()
        if (Build.VERSION.SDK_INT >= 34) overrideActivityTransition(OVERRIDE_TRANSITION_CLOSE, 0, android.R.anim.fade_out)
        else @Suppress("DEPRECATION") overridePendingTransition(0, android.R.anim.fade_out)
    }
}

data class AskCallbacks(
    val setText: (String) -> Unit,
    val listen: () -> Unit,
    val stop: () -> Unit,
    val send: () -> Unit,
    val openChat: () -> Unit,
    val clearAbout: () -> Unit,
    val setHandsFree: (Boolean) -> Unit,
    val download: () -> Boolean,
    val close: () -> Unit,
)

/** The scrim and the card at the bottom of the screen; a tap on the scrim closes it and sends nothing. */
@Composable
fun AskOverlay(ui: AskUi, cb: AskCallbacks, focusText: Boolean = false, blocked: String? = null) {
    BackHandler(onBack = cb.close)
    Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.32f))
        .clickable(remember { MutableInteractionSource() }, null, onClickLabel = stringResource(R.string.askui_close)) { cb.close() }) {
        Surface(shape = MaterialTheme.shapes.extraLarge, color = MaterialTheme.colorScheme.surfaceContainerHigh, tonalElevation = 3.dp,
            shadowElevation = 6.dp,
            modifier = Modifier.align(Alignment.BottomCenter).windowInsetsPadding(WindowInsets.navigationBars.union(WindowInsets.ime))
                .padding(12.dp).widthIn(max = 600.dp).fillMaxWidth()
                .clickable(remember { MutableInteractionSource() }, null) {}) {
            if (blocked != null) AskBlockedCard(blocked, cb.close) else AskCard(ui, cb, focusText)
        }
    }
}

/** The assistant is off for this account: the server's message and Close, nothing to send. */
@Composable
fun AskBlockedCard(message: String, close: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    Column(Modifier.fillMaxWidth().padding(bottom = 12.dp)) {
        Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 12.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(40.dp).background(cs.surfaceContainerHighest, CircleShape), contentAlignment = Alignment.Center) {
                Icon(Icons.Outlined.Lock, null, Modifier.size(22.dp), tint = cs.onSurfaceVariant)
            }
            Text(stringResource(R.string.askui_title, io.github.murtazanasir.mailr.ui.assistantName()), style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f).padding(start = 12.dp))
            IconButton(onClick = close) { Icon(Icons.Outlined.Close, stringResource(R.string.askui_close)) }
        }
        Text(message, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
            .semantics { liveRegion = LiveRegionMode.Polite })
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), horizontalArrangement = Arrangement.End) {
            TextButton(onClick = close) { Text(stringResource(R.string.askui_close)) }
        }
    }
}

/** The capture card: header, the item chip, the editable words, and the microphone, hands-free and Send. */
@Composable
fun AskCard(ui: AskUi, cb: AskCallbacks, focusText: Boolean = false) {
    val cs = MaterialTheme.colorScheme
    val focus = remember { FocusRequester() }
    LaunchedEffect(focusText) { if (focusText) catching { focus.requestFocus() } }
    Column(Modifier.fillMaxWidth().padding(bottom = 12.dp)) {
        Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 12.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(40.dp).background(cs.primaryContainer, CircleShape), contentAlignment = Alignment.Center) {
                Icon(Icons.Outlined.AutoAwesome, null, Modifier.size(22.dp), tint = cs.onPrimaryContainer)
            }
            Column(Modifier.weight(1f).padding(start = 12.dp)) {
                Text(stringResource(R.string.askui_title, io.github.murtazanasir.mailr.ui.assistantName()), style = MaterialTheme.typography.titleMedium)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    if (ui.source == VoiceSource.ON_DEVICE) Icon(Icons.Outlined.Lock, null, Modifier.size(12.dp), tint = cs.onSurfaceVariant)
                    if (ui.source == VoiceSource.ON_DEVICE) Spacer(Modifier.width(4.dp))
                    if (ui.source == VoiceSource.SERVER) Icon(Icons.Outlined.Dns, null, Modifier.size(12.dp), tint = cs.onSurfaceVariant)
                    if (ui.source == VoiceSource.SERVER) Spacer(Modifier.width(4.dp))
                    Text(when (ui.source) {
                        VoiceSource.ON_DEVICE -> stringResource(R.string.askui_voice_on_device)
                        VoiceSource.SERVER -> stringResource(R.string.askui_voice_server)
                        VoiceSource.NONE -> stringResource(R.string.askui_voice_none)
                    }, style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                }
            }
            IconButton(onClick = cb.close) { Icon(Icons.Outlined.Close, stringResource(R.string.askui_close)) }
        }
        ui.about?.let { a ->
            InputChip(selected = true, onClick = cb.clearAbout,
                label = { Text(AskCaptureModel.label(a), maxLines = 1, overflow = TextOverflow.Ellipsis) },
                leadingIcon = { Icon(aboutIcon(a), null, Modifier.size(18.dp)) },
                trailingIcon = { Icon(Icons.Outlined.Close, stringResource(R.string.askui_remove_about, AskCaptureModel.label(a)), Modifier.size(16.dp)) },
                modifier = Modifier.padding(horizontal = 16.dp).widthIn(max = 420.dp))
        }
        // Dictated words put the cursor after them (not at the start of the field).
        val (field, onField) = io.github.murtazanasir.mailr.ui.components.rememberDictatedText(ui.text, cb.setText)
        OutlinedTextField(
            value = field, onValueChange = onField, enabled = !ui.sent,
            placeholder = { Text(when (ui.about) {
                is AskAbout.Approval -> stringResource(R.string.askui_hint_approval)
                null -> stringResource(R.string.askui_hint_none)
                else -> stringResource(R.string.askui_hint_item)
            }) },
            minLines = 3, maxLines = 8, shape = RoundedCornerShape(16.dp),
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp).focusRequester(focus)
                .semantics { if (ui.listening) liveRegion = LiveRegionMode.Polite },
        )
        when (val v = ui.voice) {
            is VoiceState.Listening -> {
                val level by animateFloatAsState(v.level, tween(120), label = "level") // i18n-ignore: animation label
                Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp).heightIn(min = 24.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Outlined.GraphicEq, null, Modifier.size(18.dp), tint = cs.primary)
                    Spacer(Modifier.width(8.dp))
                    Text(when {
                        ui.source == VoiceSource.SERVER -> stringResource(R.string.askui_recording)
                        else -> stringResource(R.string.askui_listening)
                    }, style = MaterialTheme.typography.labelLarge,
                        color = cs.primary, modifier = Modifier.weight(1f))
                    LinearProgressIndicator(progress = { 0.08f + 0.92f * level }, modifier = Modifier.width(72.dp).height(4.dp))
                }
            }
            VoiceState.Transcribing -> Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp).heightIn(min = 24.dp),
                verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(8.dp))
                Text(stringResource(R.string.askui_transcribing), style = MaterialTheme.typography.labelLarge, color = cs.primary)
            }
            is VoiceState.Failed -> Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(v.message, style = MaterialTheme.typography.bodySmall, color = cs.error, modifier = Modifier.weight(1f))
                if (v.download) TextButton(onClick = { cb.download() }) { Text(stringResource(R.string.askui_get_model)) }
            }
            else -> Unit
        }
        if (ui.source != VoiceSource.NONE) FilterChip(selected = ui.handsFree, onClick = { cb.setHandsFree(!ui.handsFree) },
            label = { Text(stringResource(R.string.askui_hands_free), maxLines = 1) },
            leadingIcon = if (ui.handsFree) ({ Icon(Icons.Outlined.Check, null, Modifier.size(18.dp)) }) else null,
            modifier = Modifier.padding(horizontal = 16.dp))
        Row(Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, top = 4.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (ui.source != VoiceSource.NONE) {
                if (ui.listening) FilledIconButton(onClick = cb.stop, modifier = Modifier.size(52.dp),
                    colors = IconButtonDefaults.filledIconButtonColors(containerColor = cs.errorContainer, contentColor = cs.onErrorContainer)) {
                    Icon(Icons.Outlined.Stop, stringResource(R.string.askui_stop_listening))
                } else FilledTonalIconButton(onClick = cb.listen, enabled = !ui.sent && !ui.transcribing, modifier = Modifier.size(52.dp)) {
                    Icon(Icons.Outlined.Mic, stringResource(R.string.askui_dictate))
                }
            }
            Spacer(Modifier.weight(1f))
            TextButton(onClick = cb.openChat, enabled = !ui.sent) { Text(stringResource(R.string.askui_open_chat)) }
            FilledIconButton(onClick = cb.send, enabled = ui.canSend, modifier = Modifier.size(52.dp)) {
                Icon(Icons.AutoMirrored.Filled.Send, stringResource(R.string.askui_send))
            }
        }
    }
}

private fun aboutIcon(a: AskAbout) = when (a) {
    is AskAbout.Task -> Icons.Outlined.TaskAlt
    is AskAbout.Event -> Icons.Outlined.Event
    is AskAbout.Recording -> Icons.Outlined.Mic
    is AskAbout.Conversation -> Icons.Outlined.Mail
    is AskAbout.Approval -> Icons.Outlined.VerifiedUser
    is AskAbout.Note -> Icons.Outlined.Lightbulb
}
