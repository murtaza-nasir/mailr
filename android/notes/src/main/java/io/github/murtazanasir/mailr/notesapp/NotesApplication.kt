package io.github.murtazanasir.mailr.notesapp

import io.github.murtazanasir.mailr.util.catching

import android.app.Application
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ProcessLifecycleOwner
import androidx.work.Configuration
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.AuthApi
import io.github.murtazanasir.mailr.data.api.NotesTransport
import io.github.murtazanasir.mailr.data.api.ServerMessages
import io.github.murtazanasir.mailr.data.auth.AuthManager
import io.github.murtazanasir.mailr.data.auth.LogoutWork
import io.github.murtazanasir.mailr.data.auth.SessionHost
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.notes.NotesHost
import io.github.murtazanasir.mailr.notes.NotesModule
import io.github.murtazanasir.mailr.notes.NotesWork
import io.github.murtazanasir.mailr.notesapp.data.LiveStream
import io.github.murtazanasir.mailr.notesapp.data.NotesAppDb
import io.github.murtazanasir.mailr.notesapp.data.NotesPrefs
import io.github.murtazanasir.mailr.notesapp.launcher.Shortcuts
import io.github.murtazanasir.mailr.notesapp.reminders.NoteReminders
import io.github.murtazanasir.mailr.notesapp.widget.Widgets
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Everything the Notes app runs, built once: its own device session and API client (the shared
 * sign-in module), its own Room database with the notes tables, the Notes module (sync, replay
 * queue, screens), the change stream, note reminders, launcher shortcuts and widgets.
 */
class NotesGraph(
    context: Context,
    val inMemoryDb: Boolean = false,
    /** The Notes repository's "now" and timers; tests pass a virtual timeline they move on themselves. */
    time: io.github.murtazanasir.mailr.time.Timeline = io.github.murtazanasir.mailr.time.Timeline.Real,
) {
    val context: Context = context.applicationContext
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    val prefs = NotesPrefs(this.context)
    val sessions = SessionStore(this.context, BuildConfig.DEFAULT_SERVER)
    val client = ApiClient(sessions, clientId = ApiClient.installId(this.context), userAgent = USER_AGENT,
        deviceClock = { ApiClient.clockOf(this.context) })
    /**
     * The server refused this version (426: the update banner, the queue paused) or offers a newer one
     * (GET /api/v1/app/releases: the Update available row in Settings).
     */
    val updates = io.github.murtazanasir.mailr.data.update.AppUpdates(this.context, client, app = "notes", displayName = "Mailr Notes",
        versionCode = BuildConfig.VERSION_CODE.toLong(), packageName = this.context.packageName)
    val auth = AuthManager(this.context, AuthApi(client, app = "notes"), sessions,
        retryLogout = { LogoutWork.enqueue(this.context) }, deviceName = { deviceLabel() })
    val db = NotesAppDb.create(this.context, inMemoryDb)

    /** Schedules the background drain (WorkManager); set by [NotesApplication], null in tests. */
    var drain: (() -> Unit)? = null
    val notes = NotesModule(NotesTransport(client), db, scope, scheduleDrain = { drain?.invoke() }, drainAtOnce = !inMemoryDb, time = time)
    val reminders = NoteReminders(this.context, notes.repo, client, armAlarms = !inMemoryDb)
    val live = LiveStream(client, scope, onEvent = { t, d -> notes.repo.onEvent(t, d) }, onReminders = { syncReminders() })

    /** The signed-in user's id (items and rows by someone else show their author), or null offline. */
    suspend fun meId(): Long? = catching {
        val me = client.get("me", kotlinx.serialization.json.JsonObject.serializer())
        // The account's language (Settings → You → Region), when it is set and changed.
        val lang = ((me["settings"] as? kotlinx.serialization.json.JsonObject)?.get("language") as? kotlinx.serialization.json.JsonPrimitive)?.content
        io.github.murtazanasir.mailr.i18n.AppLocales.followAccount(context, lang)
        // The assistant's name (a note's history says who changed it); a failure keeps the last one.
        io.github.murtazanasir.mailr.i18n.AssistantName.refresh { client.get("assistant/profile", kotlinx.serialization.json.JsonObject.serializer()) }
        (me["user"] as? kotlinx.serialization.json.JsonObject)?.get("id")?.let { (it as kotlinx.serialization.json.JsonPrimitive).content.toLongOrNull() }
    }.getOrNull()

    /** The server's error texts in the app's language (ServerMessages; fetched at most once a day per language). */
    fun refreshServerMessages() = scope.launch {
        ServerMessages.refresh(client, io.github.murtazanasir.mailr.i18n.AppLocales.current(context).toLanguageTag())
    }

    fun syncReminders() {
        scope.launch { catching { reminders.sync() } }
    }

    /** Send what waits, read what changed, then plan reminders and refresh widgets and shortcuts. */
    fun syncNow() = scope.launch {
        catching { notes.repo.drain() }
        catching { notes.repo.pull() }
        catching { reminders.sync() }
    }

    /**
     * Whose cache this is: a hash of the server and the account's address (never the address), for
     * the next sign-in (the same address on another server is another account).
     */
    private fun sha256(s: String): String = java.security.MessageDigest.getInstance("SHA-256")
        .digest(s.toByteArray()).joinToString("") { "%02x".format(it) }

    private fun ownerKey(server: String, addressKey: String): String =
        sha256(io.github.murtazanasir.mailr.data.auth.NonceBinding.canonicalOrigin(server) + "\n" + addressKey)

    private fun accountKey(server: String, email: String): String = ownerKey(server, sha256(email.trim().lowercase()))

    private val ownerPrefs get() = context.getSharedPreferences("notes_owner", Context.MODE_PRIVATE)

    init {
        // A cache kept by an older version names the address only ("owner"): it belongs to the server
        // the phone still names, since that changes only at the next sign-in.
        ownerPrefs.getString(OWNER_V1, null)?.let { old ->
            val e = ownerPrefs.edit().remove(OWNER_V1)
            if (ownerPrefs.getString(OWNER, null) == null) e.putString(OWNER, ownerKey(sessions.server, old))
            e.commit()
        }
    }

    /**
     * After sign-in: the cache and the unsent changes stay when the same account signs in again on
     * the same server (after an expired session); another account or server starts empty.
     */
    suspend fun adoptCache(email: String) = withContext(Dispatchers.IO) {
        val owner = ownerPrefs.getString(OWNER, null)
        val now = accountKey(sessions.server, email)
        if (owner != null && owner != now) {
            notes.repo.clear()
            prefs.forgetAccount()
            reminders.clear()
        }
        ownerPrefs.edit().putString(OWNER, now).apply()
    }

    /** The server said the session is gone (401): back to sign-in; the cache waits for the same account. */
    fun sessionExpired() {
        sessions.clear()
    }

    /** Sign-out: the device session ends on the server and the account's notes leave this phone (repo.clear). */
    suspend fun signOut() {
        catching { auth.signOut() }
        withContext(Dispatchers.IO) {
            catching { notes.repo.clear() }
            prefs.forgetAccount()
            catching { reminders.clear() }
            ownerPrefs.edit().remove(OWNER).apply()
            catching { io.github.murtazanasir.mailr.i18n.AssistantName.forget() }
        }
    }

    companion object {
        private const val OWNER = "owner_v2"
        private const val OWNER_V1 = "owner"

        /** The app and its version; "mailr-android" first, so the server counts its writes as a device's. */
        val USER_AGENT = "mailr-android-notes/${BuildConfig.VERSION_NAME}"

        /** How the server lists this device session: "Notes on Pixel 9". */
        fun deviceLabel(): String = "Notes on ${Build.MODEL.ifBlank { "Android" }}".take(90)
    }
}

class NotesApplication : Application(), Configuration.Provider, NotesHost, SessionHost {
    lateinit var graph: NotesGraph
        private set

    override val notesModule: NotesModule?
        get() = if (::graph.isInitialized && graph.sessions.session.value != null) graph.notes else null

    override val sessionStore: SessionStore get() = graph.sessions

    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder().setMinimumLoggingLevel(android.util.Log.WARN).build()

    @OptIn(FlowPreview::class)
    override fun onCreate() {
        super.onCreate()
        io.github.murtazanasir.mailr.i18n.AppText.init(this)
        ServerMessages.init(this)
        val g = NotesGraph(this)
        graph = g
        g.drain = { NotesWork.drainSoon(this) }
        NoteReminders.createChannel(this)
        g.scope.launch { g.client.signedOut.collect { g.sessionExpired() } }
        // The server takes this version again (the floor was lowered): what waited goes out.
        g.updates.onResumed = { if (g.sessions.session.value != null) g.syncNow() }

        // Widgets and the launcher follow the phone's copy: a write here, a sync, a live event.
        g.scope.launch {
            combine(g.notes.repo.notes, g.notes.repo.previews, g.notes.repo.tables) { n, p, t -> Triple(n, p, t) }
                .drop(1).debounce(400).collect { (notes, _, tables) ->
                    catching { Widgets.updateAll(this@NotesApplication) }
                    catching { Shortcuts.follow(this@NotesApplication, notes, tables) }
                    catching { g.reminders.plan() }
                }
        }

        ProcessLifecycleOwner.get().lifecycle.addObserver(object : DefaultLifecycleObserver {
            override fun onStart(owner: LifecycleOwner) {
                if (g.sessions.session.value != null) {
                    g.live.start()
                    g.syncNow()
                    g.refreshServerMessages()
                    // A newer version on the server, at most once a day (Settings shows it).
                    g.scope.launch { g.updates.check() }
                }
            }

            override fun onStop(owner: LifecycleOwner) {
                g.live.stop()
            }
        })

        // The network came back: send what waited and read what changed.
        catching {
            getSystemService(ConnectivityManager::class.java)?.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) {
                    if (g.sessions.session.value != null) g.syncNow()
                }
            })
        }

        g.scope.launch {
            g.sessions.session.collect { s ->
                if (s != null) {
                    catching { g.adoptCache(s.email) }
                    NotesAppWork.schedulePeriodic(this@NotesApplication)
                    g.syncNow()
                    g.refreshServerMessages()
                    if (ProcessLifecycleOwner.get().lifecycle.currentState.isAtLeast(androidx.lifecycle.Lifecycle.State.STARTED)) g.live.start()
                } else {
                    g.live.stop()
                    NotesAppWork.cancel(this@NotesApplication)
                    catching { Shortcuts.signedOut(this@NotesApplication) }
                    catching { Widgets.updateAll(this@NotesApplication) }
                }
            }
        }
    }
}
