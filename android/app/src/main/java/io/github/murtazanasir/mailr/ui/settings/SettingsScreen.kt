package io.github.murtazanasir.mailr.ui.settings

import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.ui.components.refuseObscuredTouches
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Rule
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.Logout
import androidx.compose.material.icons.outlined.AlternateEmail
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.CleaningServices
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Key
import androidx.compose.material.icons.outlined.PauseCircle
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.Sync
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.ForwardToInbox
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material.icons.outlined.PhoneAndroid
import androidx.compose.material.icons.outlined.Web
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import io.github.murtazanasir.mailr.look.Switch
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.BuildConfig
import io.github.murtazanasir.mailr.data.api.SessionInfo
import io.github.murtazanasir.mailr.data.settings.AppLook
import io.github.murtazanasir.mailr.data.settings.ColourSource
import io.github.murtazanasir.mailr.data.settings.NotifyMode
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ServerPrefs
import io.github.murtazanasir.mailr.data.settings.SwipeAction
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.components.Avatar
import io.github.murtazanasir.mailr.ui.components.UserAvatar
import io.github.murtazanasir.mailr.ui.util.Format

data class SettingsCallbacks(
    val back: () -> Unit,
    val update: ((Prefs) -> Prefs) -> Unit,
    val revoke: (SessionInfo) -> Unit,
    val signOut: () -> Unit,
    val openWeb: () -> Unit,
    /** Tasks: what a swipe on a task row does (the dialog of Tasks ⋮ → Swipe actions). */
    val taskSwipes: (() -> Unit)? = null,
    val taskSwipeSummary: String? = null,
    /** Remind again on the server (null: the server has no such setting; the phone's own applies). */
    val remindAgain: io.github.murtazanasir.mailr.data.api.RepeatSettings? = null,
    val setRemindAgain: ((Int) -> Unit)? = null,
    /** Push (UnifiedPush): its state and actions; null hides the rows. */
    val push: PushUi? = null,
    val pushActions: PushCallbacks = PushCallbacks(),
    /** Your picture (account picture): its state and actions; null hides the section. */
    val picture: PictureUi? = null,
    val pictureActions: PictureCallbacks = PictureCallbacks(),
    /** A newer mailr on the server (GET /api/v1/app/releases); null hides the row. */
    val updateAvailable: io.github.murtazanasir.mailr.data.update.AvailableUpdate? = null,
    /** Update: the download opens in the browser (AppUpdates.open). */
    val openUpdate: () -> Unit = {},
    /**
     * The account's notification choices (one for every device): saved on the server
     * (PATCH /me/settings). Null: only the phone's copy changes (tests, no server).
     */
    val setMailNotify: ((NotifyMode) -> Unit)? = null,
    val setApprovals: ((Boolean) -> Unit)? = null,
    /** Settings → Activity: what changed recently, read-only. Null hides the row. */
    val openActivity: (() -> Unit)? = null,
    /** The mail accounts with their health: null while loading; empty hides the rows. */
    val accounts: List<io.github.murtazanasir.mailr.data.api.MailAccount>? = emptyList(),
    /** The web's Mail accounts page, where an account is fixed. */
    val openAccounts: () -> Unit = {},
    /** Ambrose's name: the heading of its group. */
    val assistantName: String = io.github.murtazanasir.mailr.data.api.AssistantGate.DEFAULT_NAME,
    /** Settings → Ambrose → Memory (the Memory page). Null hides the group. */
    val openMemory: (() -> Unit)? = null,
    /** Settings → Ambrose → Trusted addresses. Null hides the row. */
    val openTrusted: (() -> Unit)? = null,
    /** Settings → Ambrose → Standing orders. */
    val openOrders: (() -> Unit)? = null,
    /** Settings → Ambrose → Sweep. */
    val openSweep: (() -> Unit)? = null,
    /** The server's version (GET /me `version`), beside the app's at the foot; null shows the app's only. */
    val serverVersion: String? = null,
)

@Composable
fun SettingsScreen(prefs: Prefs, name: String, email: String, server: String, sessions: List<SessionInfo>?,
                   cb: SettingsCallbacks, speakrConnected: Boolean = false) {
    var confirmSignOut by remember { mutableStateOf(false) }
    Scaffold(
        contentWindowInsets = WindowInsets(0),
        topBar = { TopAppBar(title = { Text(stringResource(R.string.settings_title)) },
            navigationIcon = { IconButton(onClick = cb.back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.settings_back)) } }) },
    ) { pad ->
        androidx.compose.runtime.CompositionLocalProvider(io.github.murtazanasir.mailr.look.LocalRowDividers provides true) { Box(Modifier.padding(pad).fillMaxSize(), contentAlignment = Alignment.TopCenter) {
            LazyColumn(Modifier.widthIn(max = 720.dp).fillMaxWidth(), contentPadding = PaddingValues(bottom = 32.dp)) {
                item {
                    ListItem(headlineContent = { Text(name.ifBlank { email }) }, supportingContent = { Text("$email\n$server") },
                        leadingContent = { UserAvatar(name, email, size = 48.dp) })
                }
                cb.updateAvailable?.let { u ->
                    item { io.github.murtazanasir.mailr.ui.update.UpdateAvailableRow("Mailr", u, cb.openUpdate) }
                }
                // The groups and names of Settings on the web (features/settings/groups.ts): You, Look,
                // Mail, Calendar & tasks, Notifications. This phone's own switches sit in them.
                cb.picture?.let { ui ->
                    item { Header(stringResource(R.string.settings_header_you)) }
                    item { PictureSection(name, email, ui, cb.pictureActions) }
                }
                item { Header(stringResource(R.string.settings_header_look)) }
                item {
                    val names = mapOf(ThemeMode.SYSTEM to stringResource(R.string.settings_theme_automatic), ThemeMode.LIGHT to stringResource(R.string.settings_theme_light),
                        ThemeMode.DARK to stringResource(R.string.settings_theme_dark))
                    fun label(m: ThemeMode) = names.getValue(m)
                    Choice(stringResource(R.string.settings_theme), label(prefs.theme), ThemeMode.entries.map { label(it) to it }) { v -> cb.update { it.copy(theme = v) } }
                }
                // ACT settings.android_style @android.settings: the web app's look or Android's Material; it applies at once.
                item {
                    val opts = listOf(stringResource(R.string.settings_style_web) to AppLook.WEB,
                        stringResource(R.string.settings_style_material) to AppLook.MATERIAL)
                    Choice(stringResource(R.string.settings_style), opts.first { it.second == prefs.look }.first, opts,
                        supporting = stringResource(R.string.settings_style_sub)) { v -> cb.update { it.copy(look = v) } }
                }
                // ACT settings.colours_follow_web @android.settings: the wallpaper (Android 12+), mailr's own, or the web's.
                item {
                    val wallpaper = android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.S
                    val opts = buildList {
                        if (wallpaper) add(stringResource(R.string.settings_colours_wallpaper) to ColourSource.WALLPAPER)
                        add(stringResource(R.string.settings_colours_mailr) to ColourSource.MAILR)
                        add(stringResource(R.string.settings_colours_web) to ColourSource.WEB)
                    }
                    val current = if (!wallpaper && prefs.colourSource == ColourSource.WALLPAPER) ColourSource.MAILR else prefs.colourSource
                    Choice(stringResource(R.string.settings_colours), opts.first { it.second == current }.first, opts,
                        supporting = stringResource(R.string.settings_colours_sub)) { v -> cb.update { it.copy(colourChoice = v) } }
                }
                item { Toggle(stringResource(R.string.settings_compact), stringResource(R.string.settings_compact_sub), prefs.compact) { v -> cb.update { it.copy(compact = v) } } }
                item { Header(stringResource(R.string.settings_header_mail)) }
                accountItems(cb.accounts, cb.openAccounts)
                item { Choice(stringResource(R.string.settings_swipe_right), prefs.swipeStart.label, SwipeAction.entries.map { it.label to it }) { v -> cb.update { it.copy(swipeStart = v) } } }
                item { Choice(stringResource(R.string.settings_swipe_left), prefs.swipeEnd.label, SwipeAction.entries.map { it.label to it }) { v -> cb.update { it.copy(swipeEnd = v) } } }
                // ACT mail.show_images @android.settings: the web's choice, or this phone's override.
                item {
                    val web = ServerPrefs.imagesLabel(prefs.imagesWeb)
                    val opts = listOf(stringResource(R.string.settings_images_as_web, web) to "", stringResource(R.string.settings_images_always_phone) to "always",
                        stringResource(R.string.settings_images_ask_phone) to "ask")
                    Choice(stringResource(R.string.settings_images), opts.firstOrNull { it.second == prefs.imagesOverride }?.first ?: opts[0].first, opts,
                        supporting = stringResource(R.string.settings_images_sub)) { v ->
                        cb.update { it.copy(imagesOverride = v) }
                    }
                }
                // Only with a Speakr connector on the server (Settings → Mail → Mail accounts on the web). Off by default.
                if (speakrConnected) item {
                    Toggle(stringResource(R.string.settings_recordings), stringResource(R.string.settings_recordings_sub, cb.assistantName),
                        prefs.recordings) { v -> cb.update { it.copy(recordings = v) } }
                }
                item { Header(stringResource(R.string.settings_header_privacy)) }
                item {
                    Toggle(stringResource(R.string.settings_hide_content),
                        stringResource(R.string.settings_hide_content_sub),
                        prefs.hideContent) { v -> cb.update { it.copy(hideContent = v) } }
                }
                if (!prefs.hideContent) item {
                    Toggle(stringResource(R.string.settings_hide_sensitive),
                        stringResource(R.string.settings_hide_sensitive_sub),
                        prefs.hideSensitive) { v -> cb.update { it.copy(hideSensitive = v) } }
                }
                item { Header(stringResource(R.string.settings_header_notifications)) }
                // ACT notification.choose @android.settings: one choice for every device, saved on the server.
                item {
                    val levels = listOf(NotifyMode.OFF, NotifyMode.VIP, NotifyMode.IMPORTANT, NotifyMode.PRIMARY, NotifyMode.ALL)
                    Choice(stringResource(R.string.settings_new_mail), prefs.notify.label, levels.map { it.label to it },
                        supporting = stringResource(R.string.settings_new_mail_sub)) { v ->
                        cb.update { it.copy(notify = v) }
                        cb.setMailNotify?.invoke(v)
                    }
                }
                item {
                    Toggle(stringResource(R.string.settings_approvals), stringResource(R.string.settings_approvals_sub),
                        prefs.notifyApprovals) { v -> cb.update { it.copy(notifyApprovals = v) }; cb.setApprovals?.invoke(v) }
                }
                item {
                    Toggle(stringResource(R.string.settings_phone_mail), stringResource(R.string.settings_phone_mail_sub),
                        !prefs.phoneMailOff) { v -> cb.update { it.copy(phoneMailOff = !v) } }
                }
                cb.push?.let { pushItems(it, cb.pushActions) }
                item { Header(stringResource(R.string.settings_header_calendar_tasks)) }
                if (cb.taskSwipes != null) {
                    item {
                        ListItem(headlineContent = { Text(stringResource(R.string.settings_task_swipes)) },
                            supportingContent = cb.taskSwipeSummary?.let { t -> { Text(t) } },
                            modifier = Modifier.clickable { cb.taskSwipes?.invoke() })
                    }
                }
                item { Toggle(stringResource(R.string.settings_event_reminders), stringResource(R.string.settings_event_reminders_sub), prefs.eventReminders) { v -> cb.update { it.copy(eventReminders = v) } } }
                item {
                    @Composable fun before(n: Int) = pluralStringResource(R.plurals.settings_reminder_minutes_before, n, n)
                    val opts = listOf(-1 to stringResource(R.string.settings_reminder_none), 0 to stringResource(R.string.settings_reminder_at_start),
                        5 to before(5), 10 to before(10), 15 to before(15), 30 to before(30), 60 to stringResource(R.string.settings_reminder_hour_before))
                    val odd = before(prefs.defaultEventReminder)
                    Choice(stringResource(R.string.settings_default_event_reminder), opts.firstOrNull { it.first == prefs.defaultEventReminder }?.second ?: odd,
                        opts.map { it.second to it.first }) { v -> cb.update { it.copy(defaultEventReminder = v) } }
                }
                item { Toggle(stringResource(R.string.settings_task_reminders), stringResource(R.string.settings_task_reminders_sub), prefs.taskReminders) { v -> cb.update { it.copy(taskReminders = v) } } }
                item {
                    Choice(stringResource(R.string.settings_morning_hour), io.github.murtazanasir.mailr.ui.calendar.QuickDates.hourLabel(prefs.morningHour),
                        (6..11).map { io.github.murtazanasir.mailr.ui.calendar.QuickDates.hourLabel(it) to it }) { v -> cb.update { it.copy(morningHour = v) } }
                }
                reminderAlertItems(prefs, cb.update, cb.remindAgain, cb.setRemindAgain)
                item { ExactAlarmRow() }
                item {
                    val ctx = androidx.compose.ui.platform.LocalContext.current
                    ListItem(headlineContent = { Text(stringResource(R.string.settings_sound)) },
                        supportingContent = { Text(stringResource(R.string.settings_sound_sub)) },
                        modifier = Modifier.clickable { io.github.murtazanasir.mailr.ui.calendar.openNotificationSettings(ctx) })
                }
                dictationItems(prefs, cb.update)
                // Settings → Ambrose, as on the web: the Memory page.
                cb.openMemory?.let { open ->
                    item { Header(cb.assistantName) }
                    item {
                        ListItem(headlineContent = { Text(stringResource(R.string.memory_title)) },
                            supportingContent = { Text(stringResource(R.string.memory_settings_sub, cb.assistantName)) },
                            leadingContent = { Icon(Icons.Outlined.Psychology, null) },
                            modifier = Modifier.testTag("settings-memory").clickable { open() })
                    }
                }
                cb.openTrusted?.let { open ->
                    if (cb.openMemory == null) item { Header(cb.assistantName) }
                    item {
                        ListItem(headlineContent = { Text(stringResource(R.string.trusted_title)) },
                            supportingContent = { Text(stringResource(R.string.trusted_settings_sub, cb.assistantName)) },
                            leadingContent = { Icon(Icons.Outlined.ForwardToInbox, null) },
                            modifier = Modifier.testTag("settings-trusted").clickable { open() })
                    }
                }
                cb.openOrders?.let { open ->
                    if (cb.openMemory == null && cb.openTrusted == null) item { Header(cb.assistantName) }
                    item {
                        ListItem(headlineContent = { Text(stringResource(R.string.orders_title)) },
                            supportingContent = { Text(stringResource(R.string.orders_settings_sub, cb.assistantName)) },
                            leadingContent = { Icon(Icons.AutoMirrored.Outlined.Rule, null) },
                            modifier = Modifier.testTag("settings-orders").clickable { open() })
                    }
                }
                cb.openSweep?.let { open ->
                    if (cb.openMemory == null && cb.openTrusted == null && cb.openOrders == null) item { Header(cb.assistantName) }
                    item {
                        ListItem(headlineContent = { Text(stringResource(R.string.sweep_title)) },
                            supportingContent = { Text(stringResource(R.string.sweep_settings_sub, cb.assistantName)) },
                            leadingContent = { Icon(Icons.Outlined.CleaningServices, null) },
                            modifier = Modifier.testTag("settings-sweep").clickable { open() })
                    }
                }
                item { Header(stringResource(R.string.settings_header_devices)) }
                if (sessions == null) item { Text(stringResource(R.string.settings_loading), Modifier.padding(horizontal = 20.dp)) }
                else items(sessions, key = { it.id }) { s ->
                    ListItem(
                        headlineContent = {
                            val kind = stringResource(if (s.device == "android") R.string.settings_device_android else R.string.settings_device_browser)
                            Text(if (s.current) stringResource(R.string.settings_device_current, kind) else kind)
                        },
                        supportingContent = { Text("${s.userAgent.take(60)}\n${stringResource(R.string.settings_device_last_active, Format.listDate(s.lastSeenAt))}") },
                        leadingContent = { Icon(if (s.device == "android") Icons.Outlined.PhoneAndroid else Icons.Outlined.Web, null) },
                        trailingContent = { if (!s.current) IconButton(onClick = { cb.revoke(s) }) { Icon(Icons.Outlined.Delete, stringResource(R.string.settings_device_sign_out)) } },
                    )
                }
                item { HorizontalDivider(Modifier.padding(vertical = 8.dp)) }
                cb.openActivity?.let { open ->
                    item {
                        ListItem(headlineContent = { Text(stringResource(R.string.settings_activity)) },
                            supportingContent = { Text(stringResource(R.string.settings_activity_sub, cb.assistantName)) },
                            leadingContent = { Icon(Icons.Outlined.History, null) }, modifier = Modifier.clickable { open() })
                    }
                }
                item {
                    ListItem(headlineContent = { Text(stringResource(R.string.settings_more_web)) },
                        supportingContent = { Text(stringResource(R.string.settings_more_web_sub, cb.assistantName)) },
                        leadingContent = { Icon(Icons.Outlined.Web, null) }, modifier = Modifier.clickable { cb.openWeb() })
                }
                item {
                    ListItem(headlineContent = { Text(stringResource(R.string.settings_sign_out), color = MaterialTheme.colorScheme.error) },
                        leadingContent = { Icon(Icons.AutoMirrored.Outlined.Logout, null, tint = MaterialTheme.colorScheme.error) },
                        modifier = Modifier.clickable { confirmSignOut = true })
                }
                item {
                    Text(cb.serverVersion?.let { stringResource(R.string.settings_version_server, BuildConfig.VERSION_NAME, it) }
                        ?: stringResource(R.string.settings_version, BuildConfig.VERSION_NAME), style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(20.dp))
                }
            }
        } }
    }
    if (confirmSignOut) {
        AlertDialog(
            onDismissRequest = { confirmSignOut = false },
            title = { Text(stringResource(R.string.settings_sign_out_title)) },
            text = { Text(stringResource(R.string.settings_sign_out_text)) },
            confirmButton = { TextButton(onClick = { confirmSignOut = false; cb.signOut() }, modifier = Modifier.refuseObscuredTouches()) { Text(stringResource(R.string.settings_sign_out)) } },
            dismissButton = { TextButton(onClick = { confirmSignOut = false }) { Text(stringResource(R.string.settings_cancel)) } },
        )
    }
}

/**
 * ACT settings.account_health @android.settings: the line over the mail accounts ("All 2 accounts are
 * syncing", "Work: Needs its password again.") and each account's state in plain words, as the server
 * says it (core/accounthealth.py). A tap opens the web's Mail accounts page, where an account is fixed.
 */
internal fun androidx.compose.foundation.lazy.LazyListScope.accountItems(
    accounts: List<io.github.murtazanasir.mailr.data.api.MailAccount>?, open: () -> Unit,
) {
    if (accounts == null) {
        item(key = "accounts-loading") {
            ListItem(headlineContent = { Text(stringResource(R.string.settings_mail_accounts)) }, supportingContent = { Text(stringResource(R.string.settings_loading)) },
                leadingContent = { Icon(Icons.Outlined.AlternateEmail, null) })
        }
        return
    }
    val shown = accounts.filter { it.enabled || it.health != null }
    if (shown.isEmpty()) return
    val sum = io.github.murtazanasir.mailr.data.api.AccountHealthText.summary(accounts)
    item(key = "accounts-summary") {
        val bad = io.github.murtazanasir.mailr.data.api.AccountHealthText.bad(sum?.state)
        ListItem(headlineContent = { Text(stringResource(R.string.settings_mail_accounts)) },
            supportingContent = sum?.let { s -> { Text(s.text, color = if (bad) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant) } },
            leadingContent = { Icon(if (bad) Icons.Outlined.ErrorOutline else Icons.Outlined.AlternateEmail, null,
                tint = if (bad) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant) },
            modifier = Modifier.testTag("account-health-line").clickable(onClickLabel = stringResource(R.string.settings_open_accounts_web)) { open() })
    }
    items(shown, key = { "account-${it.id}" }) { a ->
        val h = a.health
        val (icon, tint) = healthLook(h?.state)
        ListItem(headlineContent = { Text(a.name) },
            supportingContent = { Text(h?.text ?: if (a.enabled) "" else stringResource(R.string.settings_account_paused),
                color = if (io.github.murtazanasir.mailr.data.api.AccountHealthText.bad(h?.state)) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant) },
            leadingContent = { Icon(icon, null, Modifier.padding(start = 8.dp), tint = tint) },
            modifier = Modifier.testTag("account-${a.id}").clickable(onClickLabel = stringResource(R.string.settings_open_accounts_web)) { open() })
    }
}

@Composable
private fun healthLook(state: String?): Pair<androidx.compose.ui.graphics.vector.ImageVector, androidx.compose.ui.graphics.Color> = when (state) {
    "signin" -> Icons.Outlined.Key to MaterialTheme.colorScheme.error
    "error" -> Icons.Outlined.ErrorOutline to MaterialTheme.colorScheme.error
    "stale" -> Icons.Outlined.Schedule to MaterialTheme.colorScheme.error
    "syncing" -> Icons.Outlined.Sync to MaterialTheme.colorScheme.primary
    "paused" -> Icons.Outlined.PauseCircle to MaterialTheme.colorScheme.onSurfaceVariant
    else -> Icons.Outlined.CheckCircle to MaterialTheme.colorScheme.primary
}

/** Whether reminders can use exact alarms; tapping opens the system page to allow them. */
@Composable
private fun ExactAlarmRow() {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val owner = androidx.lifecycle.compose.LocalLifecycleOwner.current
    var exact by remember { mutableStateOf(true) }
    androidx.compose.runtime.DisposableEffect(owner) {
        val obs = androidx.lifecycle.LifecycleEventObserver { _, e ->
            if (e == androidx.lifecycle.Lifecycle.Event.ON_RESUME) exact = io.github.murtazanasir.mailr.data.reminders.AlarmScheduler(ctx).canExact()
        }
        owner.lifecycle.addObserver(obs)
        onDispose { owner.lifecycle.removeObserver(obs) }
    }
    ListItem(headlineContent = { Text(stringResource(R.string.settings_exact_timing)) },
        supportingContent = { Text(stringResource(if (exact) R.string.settings_exact_on else R.string.settings_exact_off)) },
        modifier = Modifier.clickable(enabled = !exact) { io.github.murtazanasir.mailr.ui.calendar.openExactAlarmSettings(ctx) })
}

@Composable
internal fun Header(t: String) {
    Text(t, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(start = 20.dp, top = 20.dp, bottom = 4.dp).semantics { heading() })
}

@Composable
internal fun Toggle(title: String, subtitle: String, value: Boolean, onChange: (Boolean) -> Unit) {
    ListItem(headlineContent = { Text(title) }, supportingContent = { Text(subtitle) },
        trailingContent = { Switch(value, onChange) }, modifier = Modifier.clickable { onChange(!value) })
}

@Composable
internal fun <T> Choice(title: String, current: String, options: List<Pair<String, T>>, supporting: String? = null,
                         onPick: (T) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Column {
        ListItem(headlineContent = { Text(title) },
            supportingContent = { Text(if (supporting != null) "$current\n$supporting" else current) },
            modifier = Modifier.clickable { open = true })
        DropdownMenu(open, { open = false }) {
            options.forEach { (label, v) -> DropdownMenuItem({ Text(label) }, { open = false; onPick(v) }) }
        }
    }
}
