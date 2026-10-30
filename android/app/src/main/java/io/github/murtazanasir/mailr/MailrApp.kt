package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.util.catching

import android.app.Application
import android.content.Context
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ProcessLifecycleOwner
import androidx.work.Configuration
import io.github.murtazanasir.mailr.data.agent.AgentClient
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.auth.AuthManager
import io.github.murtazanasir.mailr.data.api.CalendarApi
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.cal.CalendarRepository
import io.github.murtazanasir.mailr.data.cal.CalendarWork
import io.github.murtazanasir.mailr.data.reminders.AlarmScheduler
import io.github.murtazanasir.mailr.data.reminders.ReminderChannels
import io.github.murtazanasir.mailr.data.reminders.ReminderEngine
import io.github.murtazanasir.mailr.data.reminders.ReminderNotifier
import io.github.murtazanasir.mailr.data.db.MailrDb
import io.github.murtazanasir.mailr.data.repo.ComposeRepository
import io.github.murtazanasir.mailr.data.repo.MailRepository
import io.github.murtazanasir.mailr.data.settings.AppSettings
import io.github.murtazanasir.mailr.data.share.SelfSendNotifier
import io.github.murtazanasir.mailr.data.share.SelfSendRepository
import io.github.murtazanasir.mailr.data.share.SelfSendWork
import io.github.murtazanasir.mailr.data.share.SelfShareShortcut
import io.github.murtazanasir.mailr.data.sync.Channels
import io.github.murtazanasir.mailr.data.sync.LiveEvents
import io.github.murtazanasir.mailr.data.sync.Work
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/**
 * Everything the app needs (a small manual DI graph). Each member is built on first use, so a
 * WorkManager wake builds only what its worker touches, and the app's start builds the rest off the
 * main thread (MailrApp.onCreate). The Keystore unseal of the session runs with the first use of
 * [sessions].
 */
class AppGraph(
    context: Context,
    private val inMemoryDb: Boolean = false,
    /** The Notes repository's "now" and timers; tests pass a virtual timeline they move on themselves. */
    private val notesTime: io.github.murtazanasir.mailr.time.Timeline = io.github.murtazanasir.mailr.time.Timeline.Real,
) {
    val context: Context = context.applicationContext
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    /**
     * The app's hooks (WorkManager jobs, notifications, widgets) for each member as it is built. MailrApp
     * sets it before anything uses the graph; tests leave it null.
     */
    var wiring: AppWiring? = null
    val sessions by lazy {
        // A queue kept by an older version becomes the current server's, before any sign-in.
        SessionStore(this.context, BuildConfig.DEFAULT_SERVER).also { migrateOwner(queuePrefs, it.server) }
    }
    /**
     * The API client and the version check on it, built together: the check follows the client's
     * answers (a 426 pauses the queues) from the first request on.
     */
    private val clientAndUpdates by lazy {
        val c = ApiClient(sessions, clientId = ApiClient.installId(this.context), userAgent = "mailr-android/${BuildConfig.VERSION_NAME}",
            deviceClock = { ApiClient.clockOf(this.context) })
        c to io.github.murtazanasir.mailr.data.update.AppUpdates(this.context, c, app = "mailr", displayName = "Mailr",
            versionCode = BuildConfig.VERSION_CODE.toLong(), packageName = this.context.packageName)
            .also { wiring?.updates(it) }
    }
    val client: ApiClient get() = clientAndUpdates.first
    val api by lazy { MailrApi(client) }
    /** Attachments downloaded for a viewer app, a share or Save to Downloads. */
    val attachments by lazy { io.github.murtazanasir.mailr.data.files.AttachmentCache(this.context.cacheDir, client, api) }
    /**
     * The server refused this version (426: the update banner, the queues paused) or offers a newer
     * one (GET /api/v1/app/releases: Settings' Update available row).
     */
    val updates: io.github.murtazanasir.mailr.data.update.AppUpdates get() = clientAndUpdates.second
    internal val dbLazy = lazy { MailrDb.create(this.context, inMemoryDb) }
    val db by dbLazy
    val mail by lazy {
        MailRepository(api, db).also { m ->
            // A conversation acted on here (in the app or from its notification) loses its notification.
            m.onHandled = { ids -> io.github.murtazanasir.mailr.data.sync.MailNotifications.cancel(this.context, ids) }
            // A discarded calendar change: the calendar reads the server's state again.
            m.onDiscarded = { op ->
                if (io.github.murtazanasir.mailr.data.repo.PendingScopes.of(op) == setOf("other")) syncCalendarSoon(force = true)
            }
            // ACT notification.choose @android.settings: what notifies and the image choice are the
            // account's (GET /me); the phone keeps a copy for the offline poll (data/settings/ServerPrefs).
            scope.launch { m.me.collect { me -> me?.let { followServer(it.settings) } } }
            wiring?.mail(m)
        }
    }
    val compose by lazy { ComposeRepository(api, mail) }
    /** The composer's local copies. */
    val drafts by lazy { io.github.murtazanasir.mailr.data.repo.LocalDrafts(api, db.localDrafts()).also { wiring?.drafts(it) } }
    val settings by lazy { AppSettings(this.context) }
    val auth by lazy { AuthManager(this.context, api.auth, sessions, retryLogout = { io.github.murtazanasir.mailr.data.auth.LogoutWork.enqueue(this.context) }) }
    val agent by lazy { AgentClient(client) }
    /**
     * An instruction from the ask capture (ui/ask), waiting for the app's Assistant tab. It never
     * travels in an Intent, so another app cannot send words to the assistant.
     */
    val pendingAsk = kotlinx.coroutines.flow.MutableStateFlow<io.github.murtazanasir.mailr.ui.ask.AskRequest?>(null)
    /** Speakr recordings through mailr; used only while Settings → Recordings (Speakr) is on. */
    val speakr by lazy { io.github.murtazanasir.mailr.data.api.SpeakrApi(client) }
    internal val recorderLazy = lazy {
        io.github.murtazanasir.mailr.data.recorder.RecorderController(
            io.github.murtazanasir.mailr.data.recorder.RecordingStore(java.io.File(this.context.filesDir, "recordings")),
            capture = { io.github.murtazanasir.mailr.data.recorder.MediaCapture(this.context) }, api = { speakr }, scope = scope,
            hooks = io.github.murtazanasir.mailr.data.recorder.AndroidRecorderHooks(this.context))
    }
    /** New recording: the phone's recorder for Speakr (data/recorder). */
    val recorder by recorderLazy
    internal val liveLazy = lazy {
        LiveEvents(client, mail, scope).also { live ->
            // Live changes of notes and tables (the change stream's item and notes events).
            scope.launch { live.events.collect { notes.repo.onEvent(it.type, it.data) } }
            wiring?.live(live)
        }
    }
    val live by liveLazy
    /**
     * Notes, checklists and tables (the shared module android/notes-core): the API on this
     * app's ApiClient, Room in this app's database, the replay queue drained at once and by
     * WorkManager ([notesDrain], set by MailrApp; tests leave it null).
     */
    var notesDrain: (() -> Unit)? = null
    val notes by lazy {
        io.github.murtazanasir.mailr.notes.NotesModule(io.github.murtazanasir.mailr.data.api.NotesTransport(client), db, scope,
            scheduleDrain = { notesDrain?.invoke() }, drainAtOnce = !inMemoryDb, time = notesTime)
    }
    private var pushSet: io.github.murtazanasir.mailr.data.push.PushRegistrar? = null
    /** Push through the user's UnifiedPush distributor (data/push); tests swap in fakes. */
    var push: io.github.murtazanasir.mailr.data.push.PushRegistrar
        get() = synchronized(this) {
            pushSet ?: io.github.murtazanasir.mailr.data.push.PushRegistrar(api, io.github.murtazanasir.mailr.data.push.PushStore(this.context),
                io.github.murtazanasir.mailr.data.push.UnifiedPushDistributors(this.context),
                signedIn = { sessions.session.value != null },
                enqueueUpload = { io.github.murtazanasir.mailr.data.push.PushWork.upload(this.context) },
                pollEvery = { m -> Work.schedulePeriodicSync(this.context, m.toLong()) },
                pushWorkingChanged = { followBackgroundCadence() }).also { pushSet = it }
        }
        set(value) = synchronized(this) { pushSet = value }
    val selfSend by lazy { SelfSendRepository(this.context.filesDir, api, db.selfSends()).also { wiring?.selfSend(it) } }
    internal val calendarLazy = lazy {
        CalendarRepository(CalendarApi(client), db, mail).also { calendar ->
            // ACT reminder.repeat @android.notification: a Remind again chosen on this phone before the server had the setting
            // moves up once, while the server's is still off; afterwards the server's choice is the one.
            calendar.afterReminderSettings = { s ->
                val p = settings.prefs.first()
                if (!p.remindAgainMigrated && s.repeat != null) {
                    val up = io.github.murtazanasir.mailr.data.reminders.RemindAgain.migration(p.remindAgain, s.repeat)
                    val ok = up == null || catching { calendar.setRemindAgain(up) }.isSuccess
                    if (ok) settings.update { it.copy(remindAgainMigrated = true) }
                }
            }
            wiring?.calendar(calendar)
        }
    }
    val calendar by calendarLazy
    /**
     * Phone calendars (data/phonecal): the phone's own calendars in the app's calendar, and the busy
     * times of the ones set to send them. Off until Settings → Calendar → Phone calendars is turned on.
     */
    internal val phoneCalendarsLazy = lazy {
        io.github.murtazanasir.mailr.data.phonecal.PhoneCalendars(this.context,
            io.github.murtazanasir.mailr.data.phonecal.ProviderCalendarSource(this.context),
            io.github.murtazanasir.mailr.data.phonecal.PhoneCalendarPrefs(this.context), client, client.clientId,
            signedIn = { sessions.session.value != null },
            // Built on first read, so a wake that only follows the settings does not build the calendar repository.
            mailr = object : io.github.murtazanasir.mailr.data.phonecal.MailrEvents {
                override fun events(fromMs: Long, toMs: Long) = calendar.forPhoneMatch.events(fromMs, toMs)
                override suspend fun eventsNow(fromMs: Long, toMs: Long) = calendar.forPhoneMatch.eventsNow(fromMs, toMs)
            }).also { wiring?.phoneCalendars(it) }
    }
    val phoneCalendars by phoneCalendarsLazy
    val alarms by lazy { AlarmScheduler(this.context) }
    internal val remindersLazy = lazy {
        ReminderEngine(db.reminders(), calendar, { calendar.reminderConfig(settings.prefs.first().reminderConfig()) },
            ReminderNotifier(this.context), alarms, remote = object : io.github.murtazanasir.mailr.data.reminders.ReminderRemote {
                override suspend fun snooze(key: String, until: Long) = calendar.queueReminderSnooze(key, until)
                override suspend fun handle(keys: List<String>, undo: Boolean) = calendar.queueReminderHandle(keys, undo)
                override suspend fun add(uid: String, occurrence: String, minutes: Int, handle: List<String>) =
                    calendar.queueReminderAdd(uid, occurrence, minutes, handle)
            },
            alerts = { io.github.murtazanasir.mailr.data.reminders.RemindAgain.effective(settings.prefs.first().alertPrefs(),
                calendar.reminderSettings.value?.repeat) },
            log = io.github.murtazanasir.mailr.data.reminders.PrefsAlertLog(this.context),
            notes = { if (notesRemindHere()) noteReminders() else emptyList() })
    }
    val reminders by remindersLazy

    /**
     * Note reminders ring from the Notes app when it is installed, else from this app, so a phone with
     * both rings once. Tests set it.
     */
    var notesRemindHere: () -> Boolean = { !io.github.murtazanasir.mailr.notes.NotesApp.installed(this.context) }

    /** The user's note reminders from the Notes module's copy, for the reminder planner. */
    suspend fun noteReminders(): List<io.github.murtazanasir.mailr.data.reminders.NoteReminderSource> =
        notes.repo.noteReminders().mapNotNull { (n, r) ->
            val at = catching { java.time.OffsetDateTime.parse(r.at).toInstant().toEpochMilli() }.getOrNull() ?: return@mapNotNull null
            io.github.murtazanasir.mailr.data.reminders.NoteReminderSource(n.id, n.title, n.isList, r.key, r.at, at)
        }

    /** The account's notification and image settings into the phone's copy; once, the phone's own earlier mail choice moves up. */
    suspend fun followServer(server: kotlinx.serialization.json.JsonObject) {
        // The account's language (Settings → You → Region), when it is set and changed.
        io.github.murtazanasir.mailr.i18n.AppLocales.followAccount(context, io.github.murtazanasir.mailr.data.settings.ServerPrefs.language(server))
        val p = settings.prefs.first()
        val (migrated, patch) = io.github.murtazanasir.mailr.data.settings.ServerPrefs.migrate(p, server)
        if (patch != null) {
            val saved = catching { api.patchSettings(patch) }.getOrNull() ?: return  // the next GET /me tries again
            settings.update { io.github.murtazanasir.mailr.data.settings.ServerPrefs.apply(it.copy(notifyMigrated = true), saved) }
            return
        }
        settings.update { io.github.murtazanasir.mailr.data.settings.ServerPrefs.apply(
            it.copy(notifyMigrated = migrated.notifyMigrated, phoneMailOff = migrated.phoneMailOff), server) }
    }

    /** ACT reminder.sync @android.notification: what other devices did to reminders (after each calendar sync, and live). */
    /** GET /assistant/profile: the assistant's name for every surface (AssistantName); a failure keeps the last one. */
    suspend fun refreshAssistantName() {
        io.github.murtazanasir.mailr.i18n.AssistantName.refresh { client.get("assistant/profile", kotlinx.serialization.json.JsonObject.serializer()) }
    }

    suspend fun pullReminders() {
        catching { calendar.pullReminderStates(if (notesRemindHere()) "e,t,n" else "e,t") { reminders.applyRemote(it) } }
    }

    /**
     * Forget what this phone holds about the account: the mail and calendar cache, cached files
     * (opened attachments, photos, dictation), shares waiting to send, posted notifications and
     * reminders. [signOut] also drops unsent recordings and recent searches; after a session
     * expiry (401) those stay for the next sign-in.
     */
    suspend fun wipeLocal(signOut: Boolean) = kotlinx.coroutines.withContext(Dispatchers.IO) {
        catching { reminders.clear() }
        catching { io.github.murtazanasir.mailr.ui.components.PictureCache.clear() }
        // After an expiry the offline queue and the composer's local copies stay for the next
        // sign-in; everything else in the database goes. Sign-out clears them too.
        catching { if (signOut) db.clearAllTables() else clearAllBut(KEPT_ON_EXPIRY) }
        catching { context.cacheDir.listFiles()?.forEach { it.deleteRecursively() } }
        catching { java.io.File(context.filesDir, "self-send").deleteRecursively() }
        if (signOut) {
            catching { java.io.File(context.filesDir, "recordings").deleteRecursively() }
            catching { settings.forgetContent() }
            catching { io.github.murtazanasir.mailr.i18n.AssistantName.forget() }
        }
        catching { androidx.core.app.NotificationManagerCompat.from(context).cancelAll() }
        pendingAsk.value = null
    }

    private fun clearAllBut(keep: Set<String>) {
        val sql = db.openHelper.writableDatabase
        val tables = sql.query("SELECT name FROM sqlite_master WHERE type = 'table'").use { c ->
            buildList { while (c.moveToNext()) add(c.getString(0)) }
        }.filter { it !in keep && it != "android_metadata" && it != "room_master_table" && !it.startsWith("sqlite_") }
        sql.beginTransaction()
        try {
            tables.forEach { sql.execSQL("DELETE FROM `$it`") }
            sql.setTransactionSuccessful()
        } finally {
            sql.endTransaction()
        }
    }

    /**
     * Whose queue the kept rows are: a hash of the server and the account's address, never the
     * address (the same address on another server is another account).
     */
    private val queuePrefs by lazy { this.context.getSharedPreferences("mailr_queue", Context.MODE_PRIVATE) }

    private fun sha256(s: String): String = java.security.MessageDigest.getInstance("SHA-256")
        .digest(s.toByteArray()).joinToString("") { "%02x".format(it) }

    /** The address alone, as kept before keys included the server (the second half of [accountKey]). */
    private fun addressKey(email: String): String = sha256(email.trim().lowercase())

    private fun accountKey(server: String, email: String): String = ownerKey(server, addressKey(email))

    private fun ownerKey(server: String, addressKey: String): String =
        sha256(io.github.murtazanasir.mailr.data.auth.NonceBinding.canonicalOrigin(server) + "\n" + addressKey)

    /**
     * A queue kept by an older version names the address only ("owner"). It was kept for the server
     * the phone still names, since that only changes at the next sign-in: it becomes that server's.
     */
    private fun migrateOwner(prefs: android.content.SharedPreferences, server: String) {
        val old = prefs.getString(OWNER_V1, null) ?: return
        val e = prefs.edit().remove(OWNER_V1)
        if (prefs.getString(OWNER, null) == null) e.putString(OWNER, ownerKey(server, old))
        e.commit()
    }

    /**
     * The server said the session is gone (401): back to sign-in, the cache wiped, and the queued
     * changes and local drafts kept for the same account's next sign-in on the same server.
     */
    fun sessionExpired() {
        val who = sessions.session.value?.email
        if (who != null) queuePrefs.edit().putString(OWNER, accountKey(sessions.server, who)).apply()
        sessions.clear()
        scope.launch { wipeLocal(signOut = false) }
    }

    /**
     * After sign-in: changes kept from an expired session go out if they are this account's on this
     * server, and are dropped if another account or another server signs in.
     */
    suspend fun adoptKeptQueue(email: String) = kotlinx.coroutines.withContext(Dispatchers.IO) {
        val owner = queuePrefs.getString(OWNER, null)
        if (owner != null && owner != accountKey(sessions.server, email)) {
            catching { db.openHelper.writableDatabase.execSQL("DELETE FROM pending_ops") }
            catching { db.openHelper.writableDatabase.execSQL("DELETE FROM notes_ops") }
            catching { drafts.clear() }
        }
        queuePrefs.edit().remove(OWNER).apply()
    }

    fun startCalendarWork() = CalendarWork.schedule(context, calendarMinutes())

    /** The calendar sync's and watchdog's period now: hourly with working push, else 15 minutes. */
    fun calendarMinutes(): Int = io.github.murtazanasir.mailr.data.sync.BackgroundSchedule.calendarMinutes(push.info.value, io.github.murtazanasir.mailr.time.AppClock.millis())

    /** Reschedules the calendar sync and the watchdog when push started or stopped working. */
    fun followBackgroundCadence() {
        if (sessions.session.value != null) CalendarWork.follow(context, calendarMinutes())
    }

    /**
     * The background pull of notes and tables: the Notes app's when it is installed (its own periodic
     * sync), so a phone with both apps pulls once. This app still sends its own queued note changes,
     * and its Notes tab syncs while it is open. Tests set it.
     */
    var notesPullHere: () -> Boolean = { !io.github.murtazanasir.mailr.notes.NotesApp.installed(this.context) }

    @Volatile private var lastCalendarSync = 0L

    /** Sync calendar and tasks now unless that happened in the last two minutes. */
    fun syncCalendarSoon(force: Boolean = false) {
        val t = System.currentTimeMillis()  // clock-ok: throttle
        if (!force && t - lastCalendarSync < 120_000) return
        lastCalendarSync = t
        scope.launch { calendar.sync(); pullReminders() }
    }

    private val _foreground = MutableStateFlow(false)
    val foreground: StateFlow<Boolean> = _foreground

    fun setForeground(value: Boolean) {
        _foreground.value = value
    }
}

/** What the app attaches to each graph member when it is built (MailrApp; nothing in tests). */
interface AppWiring {
    fun mail(m: MailRepository)
    fun drafts(d: io.github.murtazanasir.mailr.data.repo.LocalDrafts)
    fun updates(u: io.github.murtazanasir.mailr.data.update.AppUpdates)
    fun selfSend(s: SelfSendRepository)
    fun calendar(c: CalendarRepository)
    fun live(l: LiveEvents)
    fun phoneCalendars(p: io.github.murtazanasir.mailr.data.phonecal.PhoneCalendars)
}

/** Tables kept when a session expires: what the user did and has not reached the server yet. */
private val KEPT_ON_EXPIRY = setOf("pending_ops", "local_drafts", "notes_ops")

/** The kept queue's owner: the server and the address; before that, the address alone. */
private const val OWNER = "owner_v2"
private const val OWNER_V1 = "owner"

class MailrApp : Application(), Configuration.Provider, io.github.murtazanasir.mailr.notes.NotesHost,
    io.github.murtazanasir.mailr.data.auth.SessionHost {
    lateinit var graph: AppGraph
        private set

    override val sessionStore: SessionStore get() = graph.sessions

    override val notesModule: io.github.murtazanasir.mailr.notes.NotesModule?
        get() = if (::graph.isInitialized && graph.sessions.session.value != null) graph.notes else null

    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder().setMinimumLoggingLevel(android.util.Log.WARN).build()

    override fun onCreate() {
        super.onCreate()
        io.github.murtazanasir.mailr.i18n.AppText.init(this)
        // The stored catalogue is decoded on first use, not here.
        io.github.murtazanasir.mailr.data.api.ServerMessages.init(this)
        // Every member of the graph is built on first use (AppGraph): a WorkManager wake builds what
        // its worker needs, and nothing below decrypts, decodes or opens a database on the main thread.
        val g = AppGraph(this)
        graph = g
        g.wiring = Wiring()
        g.notesDrain = { io.github.murtazanasir.mailr.notes.NotesWork.drainSoon(this) }
        Channels.create(this)
        ReminderChannels.create(this)
        io.github.murtazanasir.mailr.data.recorder.RecorderChannel.create(this)
        // The user's name for the assistant: shortcuts, the tile and the widgets follow it.
        io.github.murtazanasir.mailr.i18n.AssistantName.onChange = { g.scope.launch { io.github.murtazanasir.mailr.ui.ask.AssistantSurfaces.refresh(this@MailrApp) } }
        // Off the main thread: the session (Keystore unseal) and the database the first screen and
        // every worker need, so the main thread rarely waits for them.
        g.scope.launch(Dispatchers.IO) {
            g.sessions
            g.mail
            // A recording the app was killed in the middle of: stop it where its file ends and upload it.
            g.recorder.recover()
        }
        g.scope.launch { g.calendar.loadCached() }
        // Note reminders: plan again when a note's reminders or title change (the Notes module's copy).
        g.scope.launch {
            var last: List<String>? = null
            g.notes.repo.notes.collect { list ->
                val sig = list.filter { it.deletedAt == null && it.reminders.isNotEmpty() }.map { n -> n.id + n.title + n.reminders.map { it.key } }
                if (sig != last) {
                    if (last != null) catching { g.reminders.resync() }
                    last = sig
                }
            }
        }

        ProcessLifecycleOwner.get().lifecycle.addObserver(object : DefaultLifecycleObserver {
            private var seen = false

            override fun onStart(owner: LifecycleOwner) {
                g.setForeground(true)
                // Once per process, and only when the app is opened (never for a background wake): the
                // launcher shortcuts, the share shortcut and the push registration's confirmation.
                val first = !seen
                seen = true
                // The Agenda widget's weather follows the phone: its approximate location, read while mailr is open.
                io.github.murtazanasir.mailr.data.weather.WeatherWork.onForeground(this@MailrApp)
                g.scope.launch(Dispatchers.IO) {
                    if (first) io.github.murtazanasir.mailr.ui.ask.AssistantSurfaces.publishShortcuts(this@MailrApp)
                    val s = g.sessions.session.value ?: return@launch
                    if (first) SelfShareShortcut.publish(this@MailrApp, s.email)
                    kotlinx.coroutines.withContext(Dispatchers.Main) { foregroundSignedIn(g, first) }
                }
            }

            override fun onStop(owner: LifecycleOwner) {
                g.setForeground(false)
                if (g.liveLazy.isInitialized()) g.live.stop()
            }
        })

        // The live connection (data/push/LiveConnection.kt): on while the person wants it, is
        // signed in, and push through a push app does not work.
        g.scope.launch {
            kotlinx.coroutines.flow.combine(g.settings.prefs, g.sessions.session, g.push.info) { p, s, info ->
                io.github.murtazanasir.mailr.data.push.LiveConnection.wanted(p.liveConnection, s != null, info)
            }.distinctUntilChanged().collect { run -> io.github.murtazanasir.mailr.data.push.LiveConnection.apply(this@MailrApp, run) }
        }

        g.scope.launch {
            var before: Boolean? = null
            g.sessions.session.collect { s ->
                val atStart = before == null
                before = s != null
                if (s != null && atStart) {
                    // Signed in already when the process started (often for a background job): only
                    // the periodic jobs, at the current push health. The rest waits for the app to open.
                    Work.schedulePeriodicSync(this@MailrApp, g.push.pollMinutes().toLong())
                    g.startCalendarWork()
                    catching { g.phoneCalendars.follow() }
                } else if (s != null) {
                    // What waited for this sign-in (an expired session's queue and drafts) goes out now.
                    catching { g.adoptKeptQueue(s.email) }
                    Work.drainOutbox(this@MailrApp)
                    io.github.murtazanasir.mailr.data.repo.LocalDraftWork.enqueue(this@MailrApp)
                    Work.schedulePeriodicSync(this@MailrApp, g.push.pollMinutes().toLong())
                    // Register with the push app (ntfy or another UnifiedPush distributor), if there is one.
                    catching { g.push.start() }
                    SelfShareShortcut.publish(this@MailrApp, s.email)
                    g.scope.launch { g.refreshAssistantName() }
                    g.startCalendarWork()
                    catching { g.phoneCalendars.follow() }
                    g.notes.syncNow()
                    if (g.foreground.value) g.live.start()
                } else {
                    if (g.liveLazy.isInitialized()) g.live.stop()
                    Work.cancelAll(this@MailrApp)
                    catching { g.push.forget() }
                    SelfShareShortcut.remove(this@MailrApp)
                    catching { g.reminders.clear() }
                    catching { g.calendar.clear() }
                    catching { g.phoneCalendars.follow() }
                }
            }
        }
    }

    /** The app came to the front while signed in: what a background wake never pays for. */
    private fun foregroundSignedIn(g: AppGraph, first: Boolean) {
        if (!g.foreground.value || g.sessions.session.value == null) return
        g.live.start()
        Work.drainOutbox(this)
        g.scope.launch { catching { g.mail.refreshLabels() } }
        g.syncCalendarSoon()
        g.notes.syncNow()
        // A newer version on the server, at most once a day (Settings shows it).
        g.scope.launch { g.updates.check() }
        // The assistant's name, renamed on the web since the last look (Settings → Ambrose → About you).
        g.scope.launch { g.refreshAssistantName() }
        // The server's error texts in the app's language, at most once a day.
        g.scope.launch {
            io.github.murtazanasir.mailr.data.api.ServerMessages.refresh(g.client, io.github.murtazanasir.mailr.i18n.AppLocales.current(this@MailrApp).toLanguageTag())
        }
        // Push: registered again once per process so the distributor confirms the endpoint, and
        // whenever it does not work (a push app installed or fixed since the last look).
        val info = g.push.info.value
        if (first && g.foreground.value) {
            catching { g.push.start() }
            g.scope.launch { g.adoptKeptQueue(g.sessions.session.value?.email ?: return@launch) }
            io.github.murtazanasir.mailr.data.repo.LocalDraftWork.enqueue(this)
        } else if (!info.healthy && info.status != io.github.murtazanasir.mailr.data.push.PushStatus.REGISTERING) {
            g.push.start()
        }
    }

    /** The app's hooks on each graph member, attached when the member is built. */
    private inner class Wiring : AppWiring {
        override fun mail(m: MailRepository) {
            m.onQueued = { Work.drainOutbox(this@MailrApp) }
        }

        override fun drafts(d: io.github.murtazanasir.mailr.data.repo.LocalDrafts) {
            d.onUnsynced = { io.github.murtazanasir.mailr.data.repo.LocalDraftWork.enqueue(this@MailrApp) }
        }

        override fun updates(u: io.github.murtazanasir.mailr.data.update.AppUpdates) {
            // The server takes this version again (the floor was lowered): what waited goes out.
            u.onResumed = {
                Work.drainOutbox(this@MailrApp)
                io.github.murtazanasir.mailr.data.repo.LocalDraftWork.enqueue(this@MailrApp)
                graph.notes.syncNow()
            }
        }

        override fun selfSend(s: SelfSendRepository) {
            s.enqueue = { id -> SelfSendWork.enqueue(this@MailrApp, id) }
            s.onFailed = { e -> SelfSendNotifier.failed(this@MailrApp, e) }
            s.onSent = { SelfShareShortcut.used(this@MailrApp) }
        }

        override fun calendar(c: CalendarRepository) {
            c.onChanged = {
                graph.reminders.resync()
                // The Today and Agenda widgets show the same copy (notes #15 and #16).
                io.github.murtazanasir.mailr.ui.today.TodayWidgets.refresh(this@MailrApp)
                io.github.murtazanasir.mailr.ui.agenda.AgendaWidgets.refresh(this@MailrApp)
            }
            c.scheduleDelayed = { op, delay -> CalendarWork.delayed(this@MailrApp, op, delay) }
            c.cancelDelayed = { name -> CalendarWork.cancel(this@MailrApp, name) }
        }

        override fun phoneCalendars(p: io.github.murtazanasir.mailr.data.phonecal.PhoneCalendars) {
            p.scheduleSoon = { delay -> io.github.murtazanasir.mailr.data.phonecal.PhoneCalendarWork.soon(this@MailrApp, delay) }
            p.schedulePeriodic = { on -> io.github.murtazanasir.mailr.data.phonecal.PhoneCalendarWork.periodic(this@MailrApp, on) }
        }

        override fun live(l: LiveEvents) {
            l.onEntities = { entities ->
                if ("reminder" in entities) graph.scope.launch { graph.pullReminders() }
                // A conversation read or archived elsewhere loses its notification here too.
                if ("conversation" in entities) graph.scope.launch { catching { io.github.murtazanasir.mailr.data.sync.MailNotifications.reconcile(graph) } }
                if (graph.calendar.wantsSync(entities)) graph.syncCalendarSoon(force = true)
            }
        }
    }
}
