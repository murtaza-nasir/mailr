package io.github.murtazanasir.mailr.ui.notes

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.R
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.notes.HomeShortcuts
import io.github.murtazanasir.mailr.notes.ui.NotesHostCallbacks
import io.github.murtazanasir.mailr.notes.ui.NotesPage
import io.github.murtazanasir.mailr.notes.ui.NotesRoute
import io.github.murtazanasir.mailr.notes.ui.NotesViewModel
import io.github.murtazanasir.mailr.notes.shareText
import io.github.murtazanasir.mailr.ui.Destination
import io.github.murtazanasir.mailr.ui.MainActivity
import io.github.murtazanasir.mailr.ui.ask.AskIntents

/**
 * The Notes tab of the mailr app (`android.notes`): the shared module's screens (android/notes-core)
 * with this app's links. A link chip opens the conversation in Mail or the task in Tasks; "Open in
 * Notes app" shows when the Notes app is installed (it answers `mailr-notes://`). Export, Export CSV
 * and "Add to home screen" work as in the Notes app; a pinned shortcut opens the item here. Widgets,
 * recent-item shortcuts and the share target for notes are the Notes app's.
 */
@Composable
fun NotesTab(g: AppGraph, request: Destination.Notes?, clearRequest: () -> Unit, openConversation: (Long) -> Unit,
             openTask: (String) -> Unit) {
    val vm: NotesViewModel = viewModel { NotesViewModel(g.notes.repo) }
    val ctx = LocalContext.current
    val me by g.mail.me.collectAsStateWithLifecycle()
    LaunchedEffect(request) {
        val r = request ?: return@LaunchedEffect
        vm.home()
        vm.open(if (r.kind == "table") NotesPage.OpenTable(r.id) else NotesPage.OpenNote(r.id, r.sub))
        if (r.kind == "table" && r.sub != null) vm.open(NotesPage.OpenRow(r.id, r.sub))
        clearRequest()
    }
    val installed = remember { io.github.murtazanasir.mailr.notes.NotesApp.installed(ctx) || notesAppInstalled(ctx) }
    NotesRoute(vm, notesHost(ctx, g, installed, me?.user?.id, openConversation, openTask))
}

/** The mailr app's side of the Notes screens: its links, the ask capture, the share sheet and pinned shortcuts. */
fun notesHost(ctx: Context, g: AppGraph, notesAppInstalled: Boolean, meId: Long?, openConversation: (Long) -> Unit,
              openTask: (String) -> Unit) = NotesHostCallbacks(
    openLink = { l ->
        val ref = l.targetRef
        when (l.targetKind) {
            "conversation" -> (ref["conversation_id"] as? kotlinx.serialization.json.JsonPrimitive)?.content?.toLongOrNull()?.let(openConversation)
            "task" -> (ref["uid"] as? kotlinx.serialization.json.JsonPrimitive)?.content?.let(openTask)
            else -> Unit
        }
    },
    notesAppInstalled = notesAppInstalled,
    // ACT note.open_app @android.notes: the same note in the Notes app.
    openInNotesApp = { kind, id -> catching { ctx.startActivity(notesAppIntent(ctx, kind, id)) } },
    // ACT agent.ask_about @android.notes: the ask capture with the note attached; Send opens the chat.
    ask = { n -> ctx.startActivity(AskIntents.capture(ctx, AskAbout.Note(n.id, n.title.ifBlank { ctx.getString(if (n.isList) R.string.askui_note_chip_list else R.string.askui_note_chip_note) }), from = "note")) },
    meId = meId,
    // ACT note.add_to_home @android.notes: a pinned shortcut that opens the note or table in this app's Notes tab.
    addToHome = { kind, id -> HomeShortcuts.pinToHome(ctx, g.notes.repo, g.scope, kind, id) { k, i -> mailrNoteIntent(ctx, k, i) } },
    // ACT note.export @android.notes, ACT table.export_csv @android.notes: the text through the share sheet.
    share = { name, text, _ -> shareText(ctx, name, text) },
)

/** This app's address for a note or a table (`mailr://notes/n/<id>`, `mailr://tables/t/<id>`), for a pinned shortcut. */
fun mailrNoteIntent(ctx: Context, kind: String, id: String): Intent =
    Intent(Intent.ACTION_VIEW, Uri.parse(if (kind == "table") "mailr://tables/t/$id" else "mailr://notes/n/$id"), ctx, MainActivity::class.java)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)

/**
 * The Notes app's address for a note or a table, for the Notes app of the
 * same build only: another app that registers `mailr-notes://` never receives the ids.
 */
fun notesAppIntent(ctx: Context, kind: String, id: String): Intent =
    Intent(Intent.ACTION_VIEW, Uri.parse("mailr-notes://$kind/$id")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        .setPackage(io.github.murtazanasir.mailr.data.auth.Handover.notesPackageFor(ctx.packageName))

/** Whether an app answers `mailr-notes://` (the Notes app). */
fun notesAppInstalled(ctx: Context): Boolean =
    catching { ctx.packageManager.queryIntentActivities(notesAppIntent(ctx, "note", "n"), 0).isNotEmpty() }.getOrDefault(false)
