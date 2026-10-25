package io.github.murtazanasir.mailr.notesapp.ui

import io.github.murtazanasir.mailr.util.catching

import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.i18n.AppText
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Lock
import io.github.murtazanasir.mailr.look.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedButton
import io.github.murtazanasir.mailr.look.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import io.github.murtazanasir.mailr.look.Switch
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import io.github.murtazanasir.mailr.data.auth.Session
import io.github.murtazanasir.mailr.notes.HomeShortcuts
import io.github.murtazanasir.mailr.notes.shareText
import io.github.murtazanasir.mailr.notes.data.NotesHttpException
import io.github.murtazanasir.mailr.notes.ui.NotesHostCallbacks
import io.github.murtazanasir.mailr.notes.ui.NotesPage
import io.github.murtazanasir.mailr.notes.ui.NotesRoute
import io.github.murtazanasir.mailr.notes.ui.NotesViewModel
import io.github.murtazanasir.mailr.notesapp.BuildConfig
import io.github.murtazanasir.mailr.notesapp.NotesGraph
import io.github.murtazanasir.mailr.notesapp.NotesLink
import io.github.murtazanasir.mailr.notesapp.NotesTarget
import io.github.murtazanasir.mailr.notesapp.R
import io.github.murtazanasir.mailr.notesapp.data.ThemeChoice
import io.github.murtazanasir.mailr.notesapp.launcher.Shortcuts
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingBrand
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingRoute
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingViewModel
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import java.io.IOException

/** The Notes app on the shared sign-in screen (android/session-core). */
val NotesBrand: OnboardingBrand
    get() = OnboardingBrand(AppText.get(R.string.app_name), AppText.get(R.string.notesapp_brand_tagline), R.drawable.ic_launcher_monochrome)

/** Where a link leads once checked against the account and the phone's copy. */
sealed interface LinkRoute {
    data class Open(val pages: List<NotesPage>) : LinkRoute
    data class Create(val kind: String) : LinkRoute
    /** "Not available to <account>": another account's item, another server, or gone. */
    data class Unavailable(val message: String) : LinkRoute
    data object Nothing : LinkRoute
}

/**
 * Deep-link routing (ACT note.open_app @android.notesapp): an https link only for the signed-in
 * server; a note or table the phone holds opens at once (one in Trash opens in its Trash state);
 * one it does not hold is read from the server, and a 403 or 404 there shows "Not available to
 * <account>". Offline and not on the phone: "Connect to open it".
 */
suspend fun resolveLink(g: NotesGraph, link: NotesLink, session: Session): LinkRoute {
    val repo = g.notes.repo
    val who = session.email.ifBlank { AppText.get(R.string.notesapp_this_account) }
    if (link.host != null) {
        val server = catching { Uri.parse(session.server).host }.getOrNull()
        if (server == null || !server.equals(link.host, ignoreCase = true)) return LinkRoute.Unavailable(AppText.get(R.string.notesapp_not_available_to, who))
    }
    suspend fun <T> fetch(cached: T?, refresh: suspend () -> Unit, again: suspend () -> T?): Pair<T?, String?> {
        if (cached != null) return cached to null
        return try {
            // Asked straight (the repository's own refresh stays quiet about errors), then kept on the phone.
            refresh()
            (again() ?: return null to AppText.get(R.string.notesapp_not_available_to, who)) to null
        } catch (e: NotesHttpException) {
            null to if (e.code == 403 || e.code == 404) AppText.get(R.string.notesapp_not_available_to, who) else AppText.get(R.string.notesapp_cannot_open_now)
        } catch (e: IOException) {
            null to AppText.get(R.string.notesapp_not_on_phone)
        }
    }
    return when (val t = link.target) {
        is NotesTarget.Note -> {
            val (n, err) = fetch(repo.noteNow(t.id), { repo.api.note(t.id); repo.refreshNote(t.id) }, { repo.noteNow(t.id) })
            if (n == null) LinkRoute.Unavailable(err ?: AppText.get(R.string.notesapp_not_available_to, who)) else LinkRoute.Open(listOf(NotesPage.OpenNote(t.id, t.item)))
        }
        is NotesTarget.Table -> {
            val (tb, err) = fetch(repo.tableNow(t.id), { repo.api.table(t.id); repo.refreshTable(t.id) }, { repo.tableNow(t.id) })
            if (tb == null) LinkRoute.Unavailable(err ?: AppText.get(R.string.notesapp_not_available_to, who)) else LinkRoute.Open(listOf(NotesPage.OpenTable(t.id)))
        }
        is NotesTarget.Row -> {
            val (tb, err) = fetch(repo.tableNow(t.tableId), { repo.api.table(t.tableId); repo.refreshTable(t.tableId) }, { repo.tableNow(t.tableId) })
            if (tb == null) LinkRoute.Unavailable(err ?: AppText.get(R.string.notesapp_not_available_to, who))
            // `…/row/new` (the table widget's "+"): the row form for a new row.
            else LinkRoute.Open(listOf(NotesPage.OpenTable(t.tableId), NotesPage.OpenRow(t.tableId, t.rowId.takeIf { it != "new" })))
        }
        is NotesTarget.New -> LinkRoute.Create(t.kind)
        is NotesTarget.Setup -> {
            val now = SessionHostLess.host(session.server)
            if (now != null && !now.equals(SessionHostLess.host(t.server), ignoreCase = true))
                LinkRoute.Unavailable(AppText.get(R.string.notesapp_signed_in_elsewhere, now, SessionHostLess.host(t.server).toString()))
            else LinkRoute.Nothing
        }
    }
}

private object SessionHostLess {
    fun host(server: String): String? = catching { Uri.parse(server).host }.getOrNull()
}

private sealed interface Screen {
    data object Notes : Screen
    data object Settings : Screen
    data class Unavailable(val message: String) : Screen
}

/**
 * The mailr app answers mailr://notes/…: "Open in mailr" shows when it is installed. Only the mailr
 * app of the same build receives it: another app that registers `mailr://` never gets the ids.
 */
fun mailrIntent(ctx: Context, kind: String, id: String): Intent =
    mailrView(ctx, if (kind == "table") "mailr://tables/t/$id" else "mailr://notes/n/$id")

/** A `mailr://` link for the mailr app of the same build, and no other app. */
fun mailrView(ctx: Context, uri: String): Intent =
    Intent(Intent.ACTION_VIEW, Uri.parse(uri)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        .setPackage(io.github.murtazanasir.mailr.data.auth.Handover.mailrPackageFor(ctx.packageName))

fun mailrInstalled(ctx: Context): Boolean =
    catching { ctx.packageManager.queryIntentActivities(mailrIntent(ctx, "note", "n1"), 0).isNotEmpty() }.getOrDefault(false)

@Composable
fun NotesAppRoot(g: NotesGraph, links: StateFlow<NotesLink?>, consume: () -> Unit) {
    val look by g.prefs.look.collectAsStateWithLifecycle()
    val session by g.sessions.session.collectAsStateWithLifecycle()
    val link by links.collectAsStateWithLifecycle()
    NotesTheme(look) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            val s = session
            if (s == null) {
                // "Continue as <name>" when the mailr app is signed in on this phone (session-core Handover).
                val vm: OnboardingViewModel = viewModel {
                    OnboardingViewModel(g.sessions, g.auth, io.github.murtazanasir.mailr.notesapp.auth.MailrHandover(g.context),
                        deviceName = { io.github.murtazanasir.mailr.notesapp.NotesGraph.deviceLabel() })
                }
                // mailr-notes://setup?server=…: the server is filled in and checked. Other links wait for sign-in.
                val setup = (link?.target as? NotesTarget.Setup)?.server
                OnboardingRoute(vm, setup, consume, NotesBrand)
            } else SignedIn(g, s, link, consume)
        }
    }
}

@Composable
private fun SignedIn(g: NotesGraph, session: Session, link: NotesLink?, consume: () -> Unit) {
    val vm: NotesViewModel = viewModel { NotesViewModel(g.notes.repo) }
    val ctx = LocalContext.current
    var screen by remember { mutableStateOf<Screen>(Screen.Notes) }
    var meId by remember { mutableStateOf<Long?>(null) }
    LaunchedEffect(session.email) { meId = g.meId() }
    LaunchedEffect(link) {
        val l = link ?: return@LaunchedEffect
        // Resolved first, consumed after: clearing the link earlier would cancel this effect.
        val route = resolveLink(g, l, session)
        consume()
        when (val r = route) {
            is LinkRoute.Open -> {
                screen = Screen.Notes
                vm.home()
                r.pages.forEach { vm.open(it) }
            }
            is LinkRoute.Create -> {
                screen = Screen.Notes
                vm.home()
                // ACT note.create @android.noteslauncher (New note, New list), ACT table.create @android.notesapp
                if (r.kind == "table") vm.newTable("", null) else vm.newNote(r.kind)
            }
            is LinkRoute.Unavailable -> screen = Screen.Unavailable(r.message)
            LinkRoute.Nothing -> Unit
        }
    }
    // The last three opened become the launcher's dynamic shortcuts.
    val stack by vm.stack.collectAsStateWithLifecycle()
    LaunchedEffect(stack.lastOrNull()) {
        val (kind, id) = when (val p = stack.lastOrNull()) {
            is NotesPage.OpenNote -> "note" to p.id
            is NotesPage.OpenTable -> "table" to p.id
            else -> return@LaunchedEffect
        }
        if (g.prefs.opened(kind, id)) Shortcuts.opened(ctx, recentEntries(g))
    }
    val mailr = remember { mailrInstalled(ctx) }
    // While the server refuses this version (426): the update banner above every screen.
    io.github.murtazanasir.mailr.ui.update.UpdateRequiredFrame(g.updates) {
    when (val sc = screen) {
        Screen.Notes -> NotesRoute(vm, NotesHostCallbacks(
            // A link chip opens the email, event, task or recording in the mailr app when it is there.
            openLink = { l -> openInMailr(ctx, session.server, l.targetKind, l.targetRef) },
            notesAppInstalled = mailr,
            // ACT note.open_app @android.notesapp: "Open in mailr" opens the same note in the mailr app.
            openInNotesApp = { kind, id -> catching { ctx.startActivity(mailrIntent(ctx, kind, id)) } },
            otherAppLabel = stringResource(R.string.notesapp_open_in_mailr),
            addToHome = { kind, id -> pinToHome(ctx, g, kind, id) },
            drawerExtras = listOf(stringResource(R.string.notesapp_settings) to { screen = Screen.Settings }),
            meId = meId,
            share = { name, text, _ -> shareText(ctx, name, text) },
        ))
        Screen.Settings -> SettingsScreen(g, session, onBack = { screen = Screen.Notes })
        is Screen.Unavailable -> UnavailableScreen(sc.message, onBack = { screen = Screen.Notes })
    }
    }
}

/** The recent notes and tables as launcher entries (titles and colours from the phone's copy). */
suspend fun recentEntries(g: NotesGraph): List<HomeShortcuts.Entry> = g.prefs.recents().mapNotNull { r ->
    val (kind, id) = r.split(':', limit = 2).let { it[0] to it.getOrElse(1) { "" } }
    if (kind == "table") g.notes.repo.tableNow(id)?.takeIf { it.deletedAt == null }?.let { Shortcuts.entry(it) }
    else g.notes.repo.noteNow(id)?.takeIf { it.deletedAt == null }?.let { Shortcuts.entry(it) }
}.take(Shortcuts.RECENT)

/** ACT note.add_to_home @android.notesapp: a pinned shortcut, or a message when the launcher cannot pin. */
private fun pinToHome(ctx: Context, g: NotesGraph, kind: String, id: String) =
    HomeShortcuts.pinToHome(ctx, g.notes.repo, g.scope, kind, id) { k, i -> Shortcuts.intent(ctx, k, i) }

/**
 * A link chip: an event or a task opens in the mailr app (mailr://event/…, mailr://task/…); a
 * conversation opens its web address on the signed-in server (the mailr app takes it when it
 * handles that host, else the browser).
 */
private fun openInMailr(ctx: Context, server: String, kind: String, ref: kotlinx.serialization.json.JsonObject) {
    fun s(k: String) = (ref[k] as? kotlinx.serialization.json.JsonPrimitive)?.content
    val uri = when (kind) {
        "conversation" -> s("conversation_id")?.let { "${server.trimEnd('/')}/c/$it" }
        "event" -> s("uid")?.let { "mailr://event/$it" }
        "task" -> s("uid")?.let { "mailr://task/$it" }
        else -> null
    } ?: run {
        Toast.makeText(ctx, AppText.get(R.string.notesapp_open_in_mailr_or_web), Toast.LENGTH_SHORT).show()
        return
    }
    // A conversation is a web address (the browser or the mailr app); an event or a task goes only to the mailr app.
    val intent = if (uri.startsWith("mailr://")) mailrView(ctx, uri)
    else Intent(Intent.ACTION_VIEW, Uri.parse(uri)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    catching { ctx.startActivity(intent) }
        .onFailure { Toast.makeText(ctx, AppText.get(R.string.notesapp_install_mailr), Toast.LENGTH_SHORT).show() }
}

@Composable
private fun UnavailableScreen(message: String, onBack: () -> Unit) {
    BackHandler(onBack = onBack)
    Column(Modifier.fillMaxSize().padding(32.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        Icon(Icons.Outlined.Lock, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(16.dp))
        Text(message, style = MaterialTheme.typography.titleMedium, textAlign = TextAlign.Center, modifier = Modifier.testTag("unavailable"))
        Spacer(Modifier.height(24.dp))
        Button(onClick = onBack) { Text(stringResource(R.string.notesapp_back_to_notes)) }
    }
}

/** The Notes app's settings: the account, the look, sign-out. */
@Composable
fun SettingsScreen(g: NotesGraph, session: Session, onBack: () -> Unit) {
    BackHandler(onBack = onBack)
    val look by g.prefs.look.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()
    var signingOut by remember { mutableStateOf(false) }
    val updateOffer by g.updates.available.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    Scaffold(topBar = {
        TopAppBar(title = { Text(stringResource(R.string.notesapp_settings)) }, navigationIcon = {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.notesapp_back)) }
        })
    }) { pad ->
        Column(Modifier.padding(pad).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp)) {
            // A newer version on the server: Update opens the download in the browser.
            // i18n-ignore: product name
            io.github.murtazanasir.mailr.ui.update.UpdateAvailableRow("Mailr Notes", updateOffer, onUpdate = {
                if (!g.updates.open(ctx)) Toast.makeText(ctx, AppText.get(R.string.notesapp_no_app_for_download), Toast.LENGTH_SHORT).show()
            })
            Section(stringResource(R.string.notesapp_account))
            Text(session.name.ifBlank { session.email }, style = MaterialTheme.typography.bodyLarge)
            if (session.name.isNotBlank()) Text(session.email, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(session.server.removePrefix("https://"), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(8.dp))
            OutlinedButton(enabled = !signingOut, onClick = {
                signingOut = true
                scope.launch { g.signOut() }
            }, modifier = Modifier.testTag("sign-out")) { Text(stringResource(R.string.notesapp_sign_out)) }
            Text(stringResource(R.string.notesapp_sign_out_hint), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp))
            HorizontalDivider(Modifier.padding(vertical = 16.dp))
            Section(stringResource(R.string.notesapp_theme))
            ThemeChoice.entries.forEach { c ->
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
                    .selectable(look.theme == c, role = Role.RadioButton) { g.prefs.setLook(look.copy(theme = c)) }) {
                    RadioButton(look.theme == c, null)
                    Spacer(Modifier.width(12.dp))
                    Text(stringResource(c.labelRes))
                }
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)
                    .toggleable(look.dynamicColour, role = Role.Switch) { g.prefs.setLook(look.copy(dynamicColour = it)) }) {
                    Column(Modifier.weight(1f)) {
                        Text(stringResource(R.string.notesapp_colours_from_the_wallpaper))
                        Text(stringResource(R.string.notesapp_dynamic_colour_hint), style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Switch(look.dynamicColour, null)
                }
            }
            HorizontalDivider(Modifier.padding(vertical = 16.dp))
            // The mailr app's "Hide in Recents and screenshots".
            Section(stringResource(R.string.notesapp_privacy))
            val hide by g.prefs.hideContent.collectAsStateWithLifecycle()
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)
                .toggleable(hide, role = Role.Switch) { g.prefs.setHideContent(it) }.testTag("hide-content")) {
                Column(Modifier.weight(1f)) {
                    Text(stringResource(R.string.notesapp_hide_content))
                    Text(stringResource(R.string.notesapp_hide_content_sub), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Switch(hide, null)
            }
            HorizontalDivider(Modifier.padding(vertical = 16.dp))
            Section(stringResource(R.string.notesapp_about))
            Text(stringResource(R.string.notesapp_about_version, BuildConfig.VERSION_NAME), style = MaterialTheme.typography.bodyMedium)
            Text(stringResource(R.string.notesapp_about_widgets),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp, bottom = 24.dp))
        }
    }
}

@Composable
private fun Section(text: String) {
    Text(text, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.semantics { heading() }.padding(top = 8.dp, bottom = 8.dp))
}
