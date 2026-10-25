package io.github.murtazanasir.mailr.data.settings

import androidx.annotation.StringRes
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

enum class ThemeMode { SYSTEM, LIGHT, DARK }

/** Where the app's colours come from (Settings → Look → Colours). */
enum class ColourSource { WALLPAPER, MAILR, WEB }

/**
 * The design the app wears (Settings → Look → Style): the web app's own (the default, new and
 * existing installs alike) or Android's Material. Its colours follow from it until the user picks
 * some ([Prefs.colourChoice]).
 */
enum class AppLook {
    WEB, MATERIAL;

    /** The colours this look takes while the user has not picked any. */
    val defaultColours: ColourSource get() = if (this == WEB) ColourSource.WEB else ColourSource.WALLPAPER
}
enum class SwipeAction(@StringRes val labelRes: Int) {
    ARCHIVE(R.string.settings_swipe_archive), TRASH(R.string.settings_swipe_delete), READ(R.string.settings_swipe_read),
    SNOOZE(R.string.settings_swipe_snooze), STAR(R.string.settings_swipe_star), NONE(R.string.settings_swipe_none);

    val label: String get() = AppText.get(labelRes)
}
inline fun <reified E : Enum<E>> String?.enumOr(default: E): E =
    this?.let { v -> enumValues<E>().firstOrNull { it.name == v } } ?: default

/** The account's mail notification levels (the server's `mail_notifications`), with the web's words. */
enum class NotifyMode(@StringRes val labelRes: Int, val server: String) {
    IMPORTANT(R.string.settings_notify_important, "important"), VIP(R.string.settings_notify_vip, "vip"),
    PRIMARY(R.string.settings_notify_primary, "primary"), ALL(R.string.settings_notify_all, "all"), OFF(R.string.settings_notify_off, "off");

    val label: String get() = AppText.get(labelRes)

    companion object {
        fun of(server: String?): NotifyMode? = entries.firstOrNull { it.server == server }
    }
}

data class Prefs(
    val theme: ThemeMode = ThemeMode.SYSTEM,
    val look: AppLook = AppLook.WEB,
    /** The colours the user picked in Settings; null follows [look] ([AppLook.defaultColours]). */
    val colourChoice: ColourSource? = null,
    /**
     * The web's colours (the account's `ui_accent` in its `ui_theme`, resolved by [ServerPrefs.webColours]):
     * a hex seed ("" until the first GET /me) and its harmony. A copy, so the colours stay offline.
     */
    val webSeed: String = "",
    val webHarmony: String = "single",
    val swipeStart: SwipeAction = SwipeAction.ARCHIVE,
    val swipeEnd: SwipeAction = SwipeAction.ARCHIVE,
    /**
     * The account's mail notification level (`mail_notifications`, one choice for every device; the
     * server's core/notifyprefs.py). A copy of the server's value, kept for the poll that runs without
     * a network; Settings changes it on the server ([ServerPrefs]).
     */
    val notify: NotifyMode = NotifyMode.IMPORTANT,
    /** The account's `notify_approvals` (approvals and failed sends), a copy as [notify]. */
    val notifyApprovals: Boolean = true,
    /** This phone only: no mail notifications here, whatever the account's level (the one per-device override). */
    val phoneMailOff: Boolean = false,
    /** The phone's own earlier mail choice was carried to the account once ([ServerPrefs.migrate]). */
    val notifyMigrated: Boolean = false,
    /** The account's remote-image choice on the web (`images`: always, ask, never), a copy. */
    val imagesWeb: String = "always",
    /**
     * This phone's override of [imagesWeb]: "" follows the web, "always" or "ask". Images always load
     * through mailr's image proxy, which fetched them when the mail arrived, so senders cannot see when
     * mail is opened. Spam always asks.
     */
    val imagesOverride: String = "",
    val compact: Boolean = false,
    val recentSearches: List<String> = emptyList(),
    // Calendar & tasks
    val eventReminders: Boolean = true,
    val taskReminders: Boolean = true,
    /** Minutes before, for timed events that carry no reminder of their own; -1 = none. */
    val defaultEventReminder: Int = 10,
    /** Hour for date-only task reminders and "Tomorrow morning". */
    val morningHour: Int = 9,
    val calendarView: String = "AGENDA",
    val notificationsAsked: Boolean = false,
    /** The short setup after the first sign-in (notifications, battery, push app) was shown. */
    val phoneSetupDone: Boolean = false,
    /**
     * Live connection (data/push/LiveConnection.kt): with no push app working, keep a connection to
     * the server open in a foreground service, so mail arrives at once. Off by default (battery).
     */
    val liveConnection: Boolean = false,
    val exactAlarmBannerDismissed: Boolean = false,
    /**
     * Recordings (Speakr): off by default. While off, nothing about recordings shows and nothing
     * is fetched from /speakr (Settings asks only whether a Speakr connector exists).
     */
    val recordings: Boolean = false,
    // Reminder alerts
    /** Imminent events and due tasks take the screen (a full-screen alert); off by default. */
    val fullScreenAlerts: Boolean = false,
    /** Minutes between repeat alerts of a reminder nobody has handled; 0 = off. */
    val remindAgain: Int = 0,
    /** The device's Remind again was moved up to the server's setting (once). */
    val remindAgainMigrated: Boolean = false,
    /** The third button on reminder notifications is Ask (the picker button stays in the sheet). */
    val askOnReminders: Boolean = true,
    /** A quiet notification at the morning hour with the day's events and tasks; off by default. */
    val morningAgenda: Boolean = false,
    // Dictation
    val dictationOrder: io.github.murtazanasir.mailr.data.voice.DictationOrder = io.github.murtazanasir.mailr.data.voice.DictationOrder.PHONE_FIRST,
    /** "Send when I stop talking". */
    val dictationHandsFree: Boolean = false,
    /** The Ask buttons start listening at once (the widget's text button never does). */
    val dictationListen: Boolean = true,
    /** The last answer of GET /me features.transcribe, for a capture opened before the app asked. */
    val serverDictation: Boolean = false,
    /**
     * The last GET /me access message while the assistant is off for this account ("" while it is
     * on), so an Ask capture opened before the app asks the server shows it at once.
     */
    val assistantBlocked: String = "",
    // Privacy
    /** Every mailr screen stays out of Recents thumbnails, screenshots and screen recordings (FLAG_SECURE). */
    val hideContent: Boolean = false,
    /** Approvals and local-only recordings stay out of them; on by default. */
    val hideSensitive: Boolean = true,
    // Send to self
    /** The labels picked on the last Send to self from this phone; the next card starts with them. */
    val selfSendLabels: List<Long> = emptyList(),
    /** Labels picked on Send to self cards, most recent first: the first quick chips. */
    val selfSendRecentLabels: List<Long> = emptyList(),
) {
    /** Where the colours come from: the user's pick, or the look's own. */
    val colourSource: ColourSource get() = colourChoice ?: look.defaultColours

    /** The web app's look (io.github.murtazanasir.mailr.look) is on. */
    val webLook: Boolean get() = look == AppLook.WEB

    /** The wallpaper's colours are asked for (the theme falls back to mailr's own before Android 12). */
    val dynamicColor: Boolean get() = colourSource == ColourSource.WALLPAPER

    /** The web's colours, when they are the source and known. */
    fun webColours(): io.github.murtazanasir.mailr.ui.theme.WebColours? =
        if (colourSource == ColourSource.WEB && io.github.murtazanasir.mailr.ui.theme.SeedScheme.isHex(webSeed))
            io.github.murtazanasir.mailr.ui.theme.WebColours(webSeed, io.github.murtazanasir.mailr.ui.theme.SeedScheme.Harmony.of(webHarmony))
        else null

    /** The mail level this phone notifies at: the account's, unless this phone turned mail off. */
    fun mailNotify(): NotifyMode = if (phoneMailOff) NotifyMode.OFF else notify

    /** Whether a message's remote images load at once on this phone (spam still asks). */
    fun showImages(): Boolean = when (imagesOverride) {
        "always" -> true
        "ask" -> false
        else -> imagesWeb == "always"
    }

    fun alertPrefs() = io.github.murtazanasir.mailr.data.reminders.AlertPrefs(fullScreen = fullScreenAlerts, remindAgain = remindAgain,
        ask = askOnReminders, morningAgenda = morningAgenda, morningHour = morningHour)

    fun reminderConfig() = io.github.murtazanasir.mailr.data.reminders.ReminderConfig(
        events = eventReminders, tasks = taskReminders,
        defaultTimedEvent = defaultEventReminder.takeIf { it >= 0 }, defaultAllDayEvent = null,
        defaultTask = true, morningHour = morningHour,
    )
}

private val Context.store: DataStore<Preferences> by preferencesDataStore("mailr_prefs")

class AppSettings(private val context: Context) {
    private object K {
        val theme = stringPreferencesKey("theme")
        /** Before 8 Oct 2026 a switch; read once as [Prefs.colourChoice] when colour_source is unset. */
        val dynamic = booleanPreferencesKey("dynamic_color")
        val colourSource = stringPreferencesKey("colour_source")
        /**
         * The user picked [colourSource] in Settings (from 9 Oct 2026). Before, every save wrote the
         * default too, so a stored "WALLPAPER" without this mark counts as no choice.
         */
        val colourChosen = booleanPreferencesKey("colour_chosen")
        val look = stringPreferencesKey("look")
        val webSeed = stringPreferencesKey("web_seed")
        val webHarmony = stringPreferencesKey("web_harmony")
        val swipeStart = stringPreferencesKey("swipe_start")
        val swipeEnd = stringPreferencesKey("swipe_end")
        val notify = stringPreferencesKey("notify")
        val notifyApprovals = booleanPreferencesKey("notify_approvals")
        /** Before 7 Oct 2026 the phone's own switch; read once as [Prefs.imagesOverride] "ask" when it was off. */
        val images = booleanPreferencesKey("images_always")
        val imagesOverride = stringPreferencesKey("images_override")
        val imagesWeb = stringPreferencesKey("images_web")
        val phoneMailOff = booleanPreferencesKey("phone_mail_off")
        val notifyMigrated = booleanPreferencesKey("notify_migrated")
        val compact = booleanPreferencesKey("compact")
        val recent = stringPreferencesKey("recent_searches")
        val eventRem = booleanPreferencesKey("event_reminders")
        val taskRem = booleanPreferencesKey("task_reminders")
        val defaultEventRem = intPreferencesKey("default_event_reminder")
        val morning = intPreferencesKey("morning_hour")
        val calView = stringPreferencesKey("calendar_view")
        val notifAsked = booleanPreferencesKey("notifications_asked")
        val phoneSetup = booleanPreferencesKey("phone_setup_done")
        val liveConnection = booleanPreferencesKey("live_connection")
        val exactBanner = booleanPreferencesKey("exact_alarm_banner_dismissed")
        val recordings = booleanPreferencesKey("recordings_speakr")
        val fullScreen = booleanPreferencesKey("full_screen_alerts")
        val remindAgain = intPreferencesKey("remind_again")
        val remindAgainMigrated = booleanPreferencesKey("remind_again_migrated")
        val askOnReminders = booleanPreferencesKey("ask_on_reminders")
        val morningAgenda = booleanPreferencesKey("morning_agenda")
        val dictationOrder = stringPreferencesKey("dictation_order")
        val dictationHandsFree = booleanPreferencesKey("dictation_hands_free")
        val dictationListen = booleanPreferencesKey("dictation_listen")
        val serverDictation = booleanPreferencesKey("server_dictation")
        val assistantBlocked = stringPreferencesKey("assistant_blocked")
        val hideContent = booleanPreferencesKey("hide_content")
        val hideSensitive = booleanPreferencesKey("hide_sensitive")
        val selfSendLabels = stringPreferencesKey("self_send_labels")
        val selfSendRecent = stringPreferencesKey("self_send_recent_labels")
    }

    private fun ids(s: String?): List<Long> = s?.split(',')?.mapNotNull { it.trim().toLongOrNull() }.orEmpty()

    val prefs: Flow<Prefs> = context.store.data.map(::read)

    companion object {
        const val RECENT_LABELS = 12

        /** The colours the user picked, from what is stored (older installs included); null: none picked. */
        internal fun colourChoiceOf(p: Preferences): ColourSource? {
            val stored = p[K.colourSource]?.let { v -> ColourSource.entries.firstOrNull { it.name == v } }
            return when {
                p[K.colourChosen] == true -> stored
                // The old default was the wallpaper: anything else was picked.
                stored != null && stored != ColourSource.WALLPAPER -> stored
                p[K.dynamic] == false -> ColourSource.MAILR
                else -> null
            }
        }
    }

    private fun read(p: Preferences) = Prefs(
        theme = p[K.theme].enumOr(ThemeMode.SYSTEM),
        look = p[K.look].enumOr(AppLook.WEB),
        colourChoice = colourChoiceOf(p),
        webSeed = p[K.webSeed] ?: "",
        webHarmony = p[K.webHarmony] ?: "single",
        swipeStart = p[K.swipeStart].enumOr(SwipeAction.ARCHIVE),
        swipeEnd = p[K.swipeEnd].enumOr(SwipeAction.ARCHIVE),
        notify = p[K.notify].enumOr(NotifyMode.IMPORTANT),
        notifyApprovals = p[K.notifyApprovals] ?: true,
        phoneMailOff = p[K.phoneMailOff] ?: false,
        notifyMigrated = p[K.notifyMigrated] ?: false,
        imagesWeb = p[K.imagesWeb] ?: "always",
        imagesOverride = p[K.imagesOverride] ?: if (p[K.images] == false) "ask" else "",
        compact = p[K.compact] ?: false,
        recentSearches = p[K.recent]?.split('\u0001')?.filter { it.isNotBlank() } ?: emptyList(),
        eventReminders = p[K.eventRem] ?: true,
        taskReminders = p[K.taskRem] ?: true,
        defaultEventReminder = p[K.defaultEventRem] ?: 10,
        morningHour = p[K.morning] ?: 9,
        calendarView = p[K.calView] ?: "AGENDA",
        notificationsAsked = p[K.notifAsked] ?: false,
        phoneSetupDone = p[K.phoneSetup] ?: false,
        liveConnection = p[K.liveConnection] ?: false,
        exactAlarmBannerDismissed = p[K.exactBanner] ?: false,
        recordings = p[K.recordings] ?: false,
        fullScreenAlerts = p[K.fullScreen] ?: false,
        remindAgain = p[K.remindAgain] ?: 0,
        remindAgainMigrated = p[K.remindAgainMigrated] ?: false,
        askOnReminders = p[K.askOnReminders] ?: true,
        morningAgenda = p[K.morningAgenda] ?: false,
        dictationOrder = p[K.dictationOrder].enumOr(io.github.murtazanasir.mailr.data.voice.DictationOrder.PHONE_FIRST),
        dictationHandsFree = p[K.dictationHandsFree] ?: false,
        dictationListen = p[K.dictationListen] ?: true,
        serverDictation = p[K.serverDictation] ?: false,
        assistantBlocked = p[K.assistantBlocked] ?: "",
        hideContent = p[K.hideContent] ?: false,
        hideSensitive = p[K.hideSensitive] ?: true,
        selfSendLabels = ids(p[K.selfSendLabels]),
        selfSendRecentLabels = ids(p[K.selfSendRecent]),
    )

    suspend fun update(block: (Prefs) -> Prefs) {
        context.store.edit { p ->
            val cur = read(p)
            val n = block(cur)
            p[K.theme] = n.theme.name
            p.remove(K.dynamic)
            p[K.look] = n.look.name
            val colours = n.colourChoice
            if (colours != null) {
                p[K.colourSource] = colours.name
                p[K.colourChosen] = true
            } else {
                p.remove(K.colourSource)
                p.remove(K.colourChosen)
            }
            p[K.webSeed] = n.webSeed
            p[K.webHarmony] = n.webHarmony
            p[K.swipeStart] = n.swipeStart.name
            p[K.swipeEnd] = n.swipeEnd.name
            p[K.notify] = n.notify.name
            p[K.notifyApprovals] = n.notifyApprovals
            p.remove(K.images)
            p[K.imagesOverride] = n.imagesOverride
            p[K.imagesWeb] = n.imagesWeb
            p[K.phoneMailOff] = n.phoneMailOff
            p[K.notifyMigrated] = n.notifyMigrated
            p[K.compact] = n.compact
            p[K.recent] = n.recentSearches.take(12).joinToString("\u0001")
            p[K.eventRem] = n.eventReminders
            p[K.taskRem] = n.taskReminders
            p[K.defaultEventRem] = n.defaultEventReminder
            p[K.morning] = n.morningHour
            p[K.calView] = n.calendarView
            p[K.notifAsked] = n.notificationsAsked
            p[K.phoneSetup] = n.phoneSetupDone
            p[K.liveConnection] = n.liveConnection
            p[K.exactBanner] = n.exactAlarmBannerDismissed
            p[K.recordings] = n.recordings
            p[K.fullScreen] = n.fullScreenAlerts
            p[K.remindAgain] = n.remindAgain
            p[K.remindAgainMigrated] = n.remindAgainMigrated
            p[K.askOnReminders] = n.askOnReminders
            p[K.morningAgenda] = n.morningAgenda
            p[K.dictationOrder] = n.dictationOrder.name
            p[K.dictationHandsFree] = n.dictationHandsFree
            p[K.dictationListen] = n.dictationListen
            p[K.serverDictation] = n.serverDictation
            p[K.assistantBlocked] = n.assistantBlocked
            p[K.hideContent] = n.hideContent
            p[K.hideSensitive] = n.hideSensitive
            p[K.selfSendLabels] = n.selfSendLabels.distinct().joinToString(",")
            p[K.selfSendRecent] = n.selfSendRecentLabels.distinct().take(RECENT_LABELS).joinToString(",")
        }
    }

    /** Sign-out: forget what the settings hold about the account's mail (recent searches, labels). */
    suspend fun forgetContent() {
        update { it.copy(recentSearches = emptyList(), assistantBlocked = "", selfSendLabels = emptyList(),
            selfSendRecentLabels = emptyList()) }
    }

    /** A Send to self went with [labels]: the next card starts with them, and they lead the quick chips. */
    suspend fun rememberSelfSendLabels(labels: List<Long>) {
        update { it.copy(selfSendLabels = labels.distinct(),
            selfSendRecentLabels = (labels + it.selfSendRecentLabels).distinct().take(RECENT_LABELS)) }
    }

    suspend fun rememberSearch(q: String) {
        val t = q.trim()
        if (t.isEmpty()) return
        update { it.copy(recentSearches = (listOf(t) + it.recentSearches.filter { s -> s != t }).take(12)) }
    }
}
