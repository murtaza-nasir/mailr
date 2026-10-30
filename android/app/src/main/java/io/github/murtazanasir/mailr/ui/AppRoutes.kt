package io.github.murtazanasir.mailr.ui

import io.github.murtazanasir.mailr.util.catching

// The routes of the app outside the tabs: sign-in, the first-run phone setup, the route names and New recording.

import io.github.murtazanasir.mailr.time.AppClock
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.pushServer
import android.Manifest
import io.github.murtazanasir.mailr.ui.compose.ComposeArgs
import io.github.murtazanasir.mailr.data.api.screener
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingViewModel
import io.github.murtazanasir.mailr.ui.onboarding.MailrBrand
import io.github.murtazanasir.mailr.ui.recordings.Recordings
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import java.net.URLEncoder
import java.time.ZonedDateTime
import io.github.murtazanasir.mailr.R

@Composable
internal fun OnboardingRoute(g: AppGraph, setupLinks: StateFlow<String?>, consumeSetup: () -> Unit) {
    val vm: OnboardingViewModel = viewModel { OnboardingViewModel(g.sessions, g.auth) }
    // A household invite from the setup link goes with single sign-on to its own server (it may make the invited account).
    val ctx = LocalContext.current
    LaunchedEffect(Unit) { g.auth.invite = { server -> io.github.murtazanasir.mailr.data.auth.PendingInvite(ctx).peek(server) } }
    // mailr://setup?server=…: the screen asks before it uses the server.
    val setup by setupLinks.collectAsStateWithLifecycle()
    io.github.murtazanasir.mailr.ui.onboarding.OnboardingRoute(vm, setup, consumeSetup, MailrBrand)
}

/** Notifications, battery and the push app, once after the first sign-in on this phone. */
@Composable
internal fun PhoneSetupRoute(g: AppGraph) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val push by g.push.info.collectAsStateWithLifecycle()
    var tick by remember { mutableStateOf(0) }
    // Back from the system settings or the permission dialog: look again.
    val owner = androidx.lifecycle.compose.LocalLifecycleOwner.current
    androidx.compose.runtime.DisposableEffect(owner) {
        val obs = androidx.lifecycle.LifecycleEventObserver { _, e -> if (e == androidx.lifecycle.Lifecycle.Event.ON_RESUME) tick++ }
        owner.lifecycle.addObserver(obs)
        onDispose { owner.lifecycle.removeObserver(obs) }
    }
    val notify = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { tick++ }
    // The server's own ntfy, if it runs one.
    val pushServer by produceState<io.github.murtazanasir.mailr.data.api.PushServer?>(null) {
        value = try { g.api.pushServer() } catch (e: kotlinx.coroutines.CancellationException) { throw e } catch (_: Exception) { null }
    }
    val live by remember { g.settings.prefs.map { it.liveConnection } }.collectAsStateWithLifecycle(false)
    val state = remember(tick, push, pushServer, live) {
        val pm = ctx.getSystemService(Context.POWER_SERVICE) as android.os.PowerManager
        io.github.murtazanasir.mailr.ui.onboarding.PhoneSetupState(
            sdk = android.os.Build.VERSION.SDK_INT,
            notificationsAllowed = io.github.murtazanasir.mailr.ui.calendar.notificationsAllowed(ctx),
            batteryExempt = pm.isIgnoringBatteryOptimizations(ctx.packageName),
            push = push.status, pushApp = push.distributorName,
            pushHint = io.github.murtazanasir.mailr.ui.settings.pushServerHint(push, pushServer),
            live = live)
    }
    io.github.murtazanasir.mailr.ui.onboarding.PhoneSetupScreen(state, io.github.murtazanasir.mailr.ui.onboarding.PhoneSetupCallbacks(
        allowNotifications = {
            if (android.os.Build.VERSION.SDK_INT >= 33) notify.launch(android.Manifest.permission.POST_NOTIFICATIONS)
            else catching { ctx.startActivity(Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, ctx.packageName)) }
        },
        openBattery = {
            catching { ctx.startActivity(Intent(android.provider.Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) }
                .onFailure { catching { ctx.startActivity(Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.fromParts("package", ctx.packageName, null))) } }
        },
        getPushApp = { openUrl(ctx, io.github.murtazanasir.mailr.ui.settings.PUSH_APPS_URL) },
        choosePushApp = { ctx.findActivity()?.let { g.push.choose(it) } },
        finish = { scope.launch { g.settings.update { it.copy(phoneSetupDone = true, notificationsAsked = true) } } },
        copyPushServer = { pushServer?.let { io.github.murtazanasir.mailr.ui.settings.copyAndOpenNtfy(ctx, it.url, push.distributor) } },
        useLive = { scope.launch { g.settings.update { it.copy(liveConnection = true) } } },
    ))
}

internal object Routes {
    const val MAIN = "main"
    const val SEARCH = "search"
    const val SETTINGS = "settings"
    const val APPROVALS = "approvals"
    const val SCREENER = "screener"
    const val ACTIVITY = "activity"
    /** Settings → Calendar → Phone calendars. */
    const val PHONE_CALENDARS = "phone-calendars"
    /** What Ambrose remembers: from Ambrose's ⋮ menu and from Settings → Ambrose. */
    const val MEMORY = "memory"
    const val TRUSTED = "trusted"
    /** Settings → Ambrose → Standing orders (on and off, the built-in order's permission). */
    const val ORDERS = "orders"
    /** Settings → Ambrose → Sweep (the rules of every bulk review, the remembered choices). */
    const val SWEEP = "sweep"
    const val RECORD = "record?uid={uid}&occ={occ}"

    fun record(uid: String?, occ: String?): String {
        fun e(v: String?) = v?.let { URLEncoder.encode(it, "UTF-8") } ?: ""
        return "record?uid=${e(uid)}&occ=${e(occ)}"
    }
    const val COMPOSE = "compose?draft={draft}&reply={reply}&mode={mode}&restore={restore}&to={to}&subject={subject}&body={body}&cc={cc}&bcc={bcc}"

    fun compose(a: ComposeArgs): String {
        fun e(v: String?) = v?.let { URLEncoder.encode(it, "UTF-8") } ?: ""
        return "compose?draft=${a.draftId ?: ""}&reply=${a.replyToMessageId ?: ""}&mode=${a.mode.orEmpty()}" +
            "&restore=${a.restoreKey.orEmpty()}&to=${e(a.to)}&subject=${e(a.subject)}&body=${e(a.body)}&cc=${e(a.cc)}&bcc=${e(a.bcc)}"
    }
}

/** New recording, on the phone's recorder (data/recorder); [rec] opens the sheet after the upload. */
@Composable
internal fun NewRecordingRoute(g: AppGraph, rec: Recordings?, uid: String?, occ: String?, onBack: () -> Unit) {
    val session by g.recorder.current.collectAsStateWithLifecycle()
    val level by g.recorder.level.collectAsStateWithLifecycle()
    val progress by g.recorder.progress.collectAsStateWithLifecycle()
    val all by g.recorder.all.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()
    val form = remember { io.github.murtazanasir.mailr.ui.recordings.RecordingForm(AppClock.now()) }
    var error by remember { mutableStateOf<String?>(null) }
    var pickDate by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) { g.recorder.recover() } }
    val events by androidx.compose.runtime.produceState(emptyList<io.github.murtazanasir.mailr.ui.recordings.EventOption>()) {
        val now = AppClock.now()
        val day = now.toLocalDate().atStartOfDay(now.zone)
        val me = setOfNotNull(g.sessions.session.value?.email?.lowercase())
        val (opts, current) = io.github.murtazanasir.mailr.ui.recordings.eventOptions(
            g.calendar.eventsNow(day.toInstant().toEpochMilli(), day.plusDays(1).toInstant().toEpochMilli()), me, now)
        value = opts
        val asked = uid?.let { u ->
            opts.firstOrNull { it.uid == u && (occ == null || it.occurrence == occ) }
                ?: g.calendar.eventsOf(u).firstOrNull { occ == null || it.occurrence == occ }?.let { io.github.murtazanasir.mailr.ui.recordings.eventOption(it, me) }
        }
        val s = g.recorder.current.value
        if (s != null && s.live) form.load(s.meta, opts) else if (form.event == null) form.pick(asked ?: current)
    }
    // Speakr's folders and tags, only while Recordings (Speakr) is on.
    val folders by androidx.compose.runtime.produceState(emptyList<io.github.murtazanasir.mailr.data.api.SpeakrLabel>(), rec) {
        if (rec != null) value = catching { g.speakr.folders() }.getOrDefault(emptyList())
    }
    val tags by androidx.compose.runtime.produceState(emptyList<io.github.murtazanasir.mailr.data.api.SpeakrLabel>(), rec) {
        if (rec != null) value = catching { g.speakr.tags() }.getOrDefault(emptyList())
    }
    val ctx = LocalContext.current
    io.github.murtazanasir.mailr.ui.recordings.NewRecordingScreen(form, session, folders, tags, events, level, session?.id?.let { progress[it] },
        all.filter { it.id != session?.id && it.state != io.github.murtazanasir.mailr.data.recorder.RecState.UPLOADED && !it.live },
        io.github.murtazanasir.mailr.ui.recordings.RecorderCallbacks(
            back = onBack,
            start = { meta ->
                error = null
                scope.launch {
                    catching { g.recorder.start(meta) }.onFailure { error = ctx.getString(R.string.ui_shell_mic_failed, it.message ?: ctx.getString(R.string.ui_shell_mic_busy)) }
                }
            },
            pause = { scope.launch { g.recorder.pause() } },
            resume = { scope.launch { g.recorder.resume() } },
            stop = { md -> g.recorder.setNotes(md); scope.launch { g.recorder.stop() } },
            notes = g.recorder::setNotes,
            meta = g.recorder::setMeta,
            pickDate = { pickDate = true },
            openRecording = { cid, rid -> if (rec != null) rec.show(cid, rid) else openUrl(ctx, g.sessions.server + "/recordings/$cid/$rid") },
            retry = g.recorder::retry,
            discard = g.recorder::discard,
            newOne = {
                g.recorder.dismiss()
                form.title = ""; form.participants = ""; form.pick(null); form.meetingDate = AppClock.now()
            },
            select = g.recorder::select,
        ), error = error)
    if (pickDate) DateTimeDialog(onDismiss = { pickDate = false }) { at -> pickDate = false; form.meetingDate = at }
}
