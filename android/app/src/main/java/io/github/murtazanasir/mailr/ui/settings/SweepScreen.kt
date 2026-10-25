package io.github.murtazanasir.mailr.ui.settings

import io.github.murtazanasir.mailr.util.catching

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
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.ReviewChoice
import io.github.murtazanasir.mailr.data.api.SWEEP_RULES_MAX
import io.github.murtazanasir.mailr.data.api.clearReviewChoices
import io.github.murtazanasir.mailr.data.api.removeReviewChoice
import io.github.murtazanasir.mailr.data.api.reviewChoices
import io.github.murtazanasir.mailr.data.api.setSweepRules
import io.github.murtazanasir.mailr.data.api.sweepRules
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import io.github.murtazanasir.mailr.look.FilledTonalButton
import io.github.murtazanasir.mailr.look.ListItem
import io.github.murtazanasir.mailr.look.OutlinedTextField
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import io.github.murtazanasir.mailr.ui.util.Format
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/** What Settings → Ambrose → Sweep reads and changes (the setting sweep_rules; GET/DELETE
 *  /agent/review-choices). Tests pass a fake. */
interface SweepStore {
    suspend fun rules(): String
    suspend fun saveRules(text: String)
    suspend fun choices(): List<ReviewChoice>
    suspend fun remove(id: Long)
    suspend fun clear()
}

class ApiSweepStore(private val g: AppGraph) : SweepStore {
    override suspend fun rules() = g.api.sweepRules()
    override suspend fun saveRules(text: String) { g.api.setSweepRules(text) }
    override suspend fun choices() = g.api.reviewChoices().items
    override suspend fun remove(id: Long) { g.api.removeReviewChoice(id) }
    override suspend fun clear() { g.api.clearReviewChoices() }
}

@Composable
fun SweepRoute(g: AppGraph, assistantName: String, onBack: () -> Unit) {
    val store = remember { ApiSweepStore(g) }
    SweepPage(store, assistantName, onBack)
}

/** The page's data: the saved rules and the remembered choices. */
data class SweepData(val rules: String, val choices: List<ReviewChoice>)

/**
 * Settings → Ambrose → Sweep, as on the web: your rules for every bulk review (Save), the built-in
 * meaning of each move that they adjust, and the moves remembered from review cards per sender, each
 * with Remove, and Clear all after a dialog.
 */
@Composable
fun SweepPage(store: SweepStore, assistantName: String, onBack: () -> Unit) {
    var data by remember { mutableStateOf<SweepData?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var clearing by remember { mutableStateOf(false) }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    suspend fun fetch() = SweepData(store.rules(), store.choices())
    fun run(block: suspend () -> String?) = scope.launch {
        busy = true
        try {
            val msg = block()
            data = fetch()
            error = null
            msg?.let { scope.launch { snackbar.showSnackbar(it) } }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            catching { data = fetch() }
            scope.launch { snackbar.showSnackbar(e.userMessage()) }
        } finally {
            busy = false
        }
    }
    fun load() = scope.launch {
        try {
            data = fetch()
            error = null
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            error = e.userMessage()
        }
    }
    LaunchedEffect(Unit) { load() }
    val cb = SweepCallbacks(
        save = { text -> run { store.saveRules(text); AppText.get(R.string.sweep_saved) } },
        remove = { c -> run { store.remove(c.id); AppText.get(R.string.sweep_removed, c.sender) } },
        clear = { clearing = true },
        retry = { load() },
    )
    SweepScreen(data, error, busy, assistantName, cb, snackbar, onBack)
    if (clearing) {
        AlertDialog(
            onDismissRequest = { clearing = false },
            title = { Text(stringResource(R.string.sweep_clear_title)) },
            text = { Text(stringResource(R.string.sweep_clear_body)) },
            confirmButton = {
                TextButton(onClick = {
                    clearing = false
                    run { store.clear(); AppText.get(R.string.sweep_cleared) }
                }, modifier = Modifier.testTag("sweep-clear-confirm")) { Text(stringResource(R.string.sweep_clear_all)) }
            },
            dismissButton = { TextButton(onClick = { clearing = false }) { Text(stringResource(R.string.sweep_cancel)) } },
        )
    }
}

class SweepCallbacks(
    val save: (String) -> Unit,
    val remove: (ReviewChoice) -> Unit,
    val clear: () -> Unit,
    val retry: () -> Unit,
)

/** A remembered move in words. */
@Composable
fun sweepMoveText(c: ReviewChoice): String = when {
    c.move.startsWith("label:") -> stringResource(R.string.sweep_move_label, c.label.orEmpty())
    c.move == "trash" -> stringResource(R.string.sweep_move_trash)
    c.move == "archive" -> stringResource(R.string.sweep_move_archive)
    c.move == "read" -> stringResource(R.string.sweep_move_read)
    else -> stringResource(R.string.sweep_move_keep)
}

@Composable
fun SweepScreen(data: SweepData?, error: String?, busy: Boolean, assistantName: String, cb: SweepCallbacks,
                snackbar: SnackbarHostState = remember { SnackbarHostState() }, onBack: () -> Unit) {
    // The field's own copy while you type; Save sends it.
    var draft by remember(data?.rules) { mutableStateOf(data?.rules.orEmpty()) }
    Scaffold(
        contentWindowInsets = WindowInsets(0),
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = { TopAppBar(title = { Text(stringResource(R.string.sweep_title)) },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.settings_back)) } }) },
    ) { pad ->
        androidx.compose.runtime.CompositionLocalProvider(io.github.murtazanasir.mailr.look.LocalRowDividers provides true) {
            Box(Modifier.padding(pad).fillMaxSize(), contentAlignment = Alignment.TopCenter) {
                LazyColumn(Modifier.widthIn(max = 720.dp).fillMaxWidth().testTag("sweep-list"), contentPadding = PaddingValues(bottom = 32.dp)) {
                    item { Hint(stringResource(R.string.sweep_intro, assistantName)) }
                    when {
                        data == null && error != null -> item {
                            Column(Modifier.padding(horizontal = 20.dp, vertical = 12.dp)) {
                                Text(error, color = MaterialTheme.colorScheme.error)
                                TextButton(onClick = cb.retry) { Text(stringResource(R.string.settings_try_again)) }
                            }
                        }
                        data == null -> item { Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() } }
                        else -> {
                            item { Header(stringResource(R.string.sweep_rules)) }
                            item {
                                Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp)) {
                                    OutlinedTextField(draft, { draft = it.take(SWEEP_RULES_MAX) }, Modifier.fillMaxWidth().testTag("sweep-rules"),
                                        minLines = 3, placeholder = { Text(stringResource(R.string.sweep_placeholder)) },
                                        supportingText = { Text(stringResource(R.string.sweep_length, draft.length, SWEEP_RULES_MAX)) })
                                    FilledTonalButton(onClick = { cb.save(draft) }, enabled = !busy && draft.trim() != data.rules,
                                        modifier = Modifier.padding(top = 4.dp).testTag("sweep-save")) { Text(stringResource(R.string.sweep_save)) }
                                }
                                Hint(stringResource(R.string.sweep_rules_hint, assistantName))
                            }
                            item { Header(stringResource(R.string.sweep_definitions)) }
                            item {
                                Column(Modifier.testTag("sweep-definitions")) {
                                    for ((move, def) in listOf(
                                        R.string.sweep_move_trash to R.string.sweep_def_trash,
                                        R.string.sweep_move_archive to R.string.sweep_def_archive,
                                        R.string.sweep_move_read to R.string.sweep_def_read,
                                        R.string.sweep_move_keep to R.string.sweep_def_keep,
                                        R.string.sweep_move_unsure to R.string.sweep_def_unsure,
                                    )) {
                                        ListItem(headlineContent = { Text(stringResource(move)) }, supportingContent = { Text(stringResource(def)) })
                                    }
                                }
                                Hint(stringResource(R.string.sweep_always_kept))
                            }
                            item { Header(stringResource(R.string.sweep_past)) }
                            item { Hint(stringResource(R.string.sweep_past_hint)) }
                            if (data.choices.isEmpty()) item { Hint(stringResource(R.string.sweep_none), Modifier.testTag("sweep-none")) }
                            items(data.choices, key = { it.id }) { c ->
                                val move = sweepMoveText(c)
                                val sub = c.updatedAt?.let { "$move · ${Format.listDate(it)}" } ?: move
                                ListItem(headlineContent = { Text(c.sender) }, supportingContent = { Text(sub) },
                                    trailingContent = {
                                        IconButton(onClick = { cb.remove(c) }, enabled = !busy, modifier = Modifier.testTag("sweep-remove")) {
                                            Icon(Icons.Outlined.Delete, stringResource(R.string.sweep_remove_for, c.sender))
                                        }
                                    }, modifier = Modifier.testTag("sweep-choice"))
                            }
                            if (data.choices.isNotEmpty()) item {
                                TextButton(onClick = cb.clear, enabled = !busy, modifier = Modifier.padding(horizontal = 8.dp).testTag("sweep-clear")) {
                                    Text(stringResource(R.string.sweep_clear_all))
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun Hint(text: String, modifier: Modifier = Modifier) {
    Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = modifier.padding(horizontal = 20.dp, vertical = 8.dp))
}
