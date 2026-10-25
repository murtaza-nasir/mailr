package io.github.murtazanasir.mailr.ui.share

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.ui.components.followSecurePrefs
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.share.ShareParser
import io.github.murtazanasir.mailr.data.share.SharedContent
import io.github.murtazanasir.mailr.ui.DateTimeDialog
import io.github.murtazanasir.mailr.ui.MainActivity
import io.github.murtazanasir.mailr.ui.compose.ComposeArgs
import io.github.murtazanasir.mailr.ui.compose.ComposeCallbacks
import io.github.murtazanasir.mailr.ui.compose.ComposeResult
import androidx.lifecycle.createSavedStateHandle
import io.github.murtazanasir.mailr.ui.compose.ComposeScreen
import io.github.murtazanasir.mailr.ui.compose.ComposeViewModel
import io.github.murtazanasir.mailr.ui.compose.RestoreStore
import io.github.murtazanasir.mailr.ui.graph
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import io.github.murtazanasir.mailr.R

/**
 * The regular share target ("New email"): the composer, prefilled with what was shared.
 * It runs in the sharing app's task, so after Send (or closing) the user is back where they
 * shared from. Files are attached (uploaded) as soon as the composer opens, while this
 * activity still holds the read grant.
 */
class ShareComposeActivity : ComponentActivity() {
    // The app's language before Android 13; a no-op from 13 on.
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(io.github.murtazanasir.mailr.i18n.AppLocales.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        followSecurePrefs(graph.settings.prefs)
        val g = graph
        if (g.sessions.session.value == null) {
            Toast.makeText(this, getString(R.string.sharing_sign_in_then_share), Toast.LENGTH_LONG).show()
            startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            finish()
            return
        }
        val shared = ShareParser.parse(intent, packageName) ?: SharedContent()
        setContent {
            val prefs by g.settings.prefs.collectAsStateWithLifecycle(Prefs())
            MailrTheme(prefs.theme, prefs.dynamicColor, prefs.webColours(), prefs.webLook) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
                    ShareCompose(g, shared, attachNow = savedInstanceState == null, finish = ::finish)
                }
            }
        }
    }

    companion object {
        /** Composer fields from a share. The subject is the shared subject/title; a body that only repeats it is dropped. */
        fun argsFor(s: SharedContent): ComposeArgs {
            val subject = s.subject ?: s.title
            val body = s.text?.takeIf { it.trim() != subject?.trim() }
            return ComposeArgs(to = s.to.joinToString(",").ifBlank { null }, subject = subject, body = body)
        }
    }
}

@Composable
private fun ShareCompose(g: AppGraph, shared: SharedContent, attachNow: Boolean, finish: () -> Unit) {
    val ctx = LocalContext.current
    // After an Undo, the composer comes back with the cancelled message (a new ViewModel key).
    var restoreKey by rememberSaveable { mutableStateOf<String?>(null) }
    var generation by rememberSaveable { mutableStateOf(0) }
    val args = restoreKey?.let { ComposeArgs(restoreKey = it) } ?: ShareComposeActivity.argsFor(shared)
    val vm: ComposeViewModel = viewModel(key = "share-compose-$generation") { ComposeViewModel(g, args, createSavedStateHandle()) }
    val s by vm.state.collectAsStateWithLifecycle()
    val sb = remember { SnackbarHostState() }
    var pickSchedule by remember { mutableStateOf(false) }
    var attached by rememberSaveable { mutableStateOf(!attachNow) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        vm.attach(ctx.contentResolver, uris)
    }
    LaunchedEffect(s.loading) {
        if (!s.loading && !attached && shared.files.isNotEmpty()) {
            attached = true
            vm.attach(ctx.contentResolver, shared.files)
        }
    }
    LaunchedEffect(s.error) { s.error?.let { sb.showSnackbar(it); vm.clearError() } }
    LaunchedEffect(vm) {
        vm.done.collect { r ->
            when (r) {
                is ComposeResult.Sent -> {
                    val res = sb.showSnackbar(ctx.getString(if (r.scheduled) R.string.sharing_scheduled else R.string.sharing_sending), ctx.getString(R.string.sharing_undo), duration = SnackbarDuration.Short)
                    if (res == SnackbarResult.ActionPerformed) {
                        catching { g.compose.cancel(r.outbox.id) }.onSuccess { c ->
                            c.payload?.let { p ->
                                restoreKey = RestoreStore.put(p, c.draftId)
                                generation++
                            }
                        }.onFailure {
                            Toast.makeText(ctx, ctx.getString(R.string.sharing_too_late_undo_sent), Toast.LENGTH_SHORT).show()
                            finish()
                        }
                    } else finish()
                }
                ComposeResult.SavedDraft, ComposeResult.SavedLocally -> {
                    Toast.makeText(ctx, ComposeResult.message(r), Toast.LENGTH_SHORT).show()
                    finish()
                }
                ComposeResult.Discarded, is ComposeResult.DiscardedDraft -> finish()
            }
        }
    }
    BackHandler { vm.close() }
    val photos = io.github.murtazanasir.mailr.ui.compose.rememberPhotoActions(vm)
    ComposeScreen(s, sb, ComposeCallbacks(
        close = vm::close, discard = vm::discard, send = { vm.send(it) }, pickSchedule = { pickSchedule = true },
        attach = { picker.launch(arrayOf("*/*")) }, setIdentity = vm::setIdentity, setSubject = vm::setSubject,
        setBody = vm::setBody, addRecipient = vm::addRecipient, addPerson = vm::addPerson,
        removeRecipient = vm::removeRecipient, query = vm::query, toggleCc = vm::toggleCc, toggleQuoted = vm::toggleQuoted,
        removeAttachment = vm::removeAttachment, restoreAttachment = vm::restoreAttachment,
        setText = vm::setText, setCursor = vm::setCursor, removeBlock = vm::removeBlock, editAsText = vm::editAsText,
        takePhoto = photos.take, pickPhotos = photos.pick,
    ))
    if (pickSchedule) DateTimeDialog(onDismiss = { pickSchedule = false }) { at -> pickSchedule = false; vm.send(at) }
}

