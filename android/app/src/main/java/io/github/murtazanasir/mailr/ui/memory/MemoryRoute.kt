package io.github.murtazanasir.mailr.ui.memory

import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.window.core.layout.WindowWidthSizeClass
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.KnownEntity
import io.github.murtazanasir.mailr.data.api.Me
import io.github.murtazanasir.mailr.i18n.AppText
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

/** The VIP senders the account set on the web (GET /me settings.vip), for the person card's VIP mark. */
fun Me?.isVip(email: String): Boolean =
    (this?.settings?.get("vip") as? JsonArray)?.any { it.jsonPrimitive.contentOrNull?.equals(email, ignoreCase = true) == true } == true

/** The Memory page with its add and edit sheet and the person card (a sheet on phones, a pane when wide). */
@Composable
fun MemoryRoute(g: AppGraph, assistantName: String, onBack: () -> Unit, openConversation: (Long) -> Unit,
                openChat: (suspend (String, Long?) -> String?)? = null) {
    val store = remember { ApiMemoryStore(g) }
    val vm: MemoryViewModel = viewModel { MemoryViewModel(store) }
    vm.assistantName = assistantName
    val ui by vm.ui.collectAsStateWithLifecycle()
    val me by g.mail.me.collectAsStateWithLifecycle()
    MemoryPage(ui, store, assistantName, vm, vip = { me.isVip(it) }, onBack = onBack, openConversation = openConversation, openChat = openChat)
}

/** The page's wiring, apart from the graph (tests and docs shots pass a fake store). */
@Composable
fun MemoryPage(
    ui: MemoryUi,
    store: MemoryStore,
    assistantName: String,
    vm: MemoryViewModel,
    vip: (String) -> Boolean,
    onBack: () -> Unit,
    openConversation: ((Long) -> Unit)?,
    /** "From a chat" on a memory: that chat in Ambrose, at the message. Returns null when it opened,
     *  else the string that says why not (shown here). */
    openChat: (suspend (String, Long?) -> String?)? = null,
    wideOverride: Boolean? = null,
    initialCard: Long? = null,
) {
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    var editing by remember { mutableStateOf<MemorySheetRequest?>(null) }
    var entities by remember { mutableStateOf<List<KnownEntity>>(emptyList()) }
    var card by rememberSaveable { mutableStateOf(initialCard) }
    val wide = wideOverride ?: (currentWindowAdaptiveInfo().windowSizeClass.windowWidthSizeClass != WindowWidthSizeClass.COMPACT)
    LaunchedEffect(vm) {
        vm.messages.collect { m ->
            scope.launch {
                val r = snackbar.showSnackbar(m.text, m.undo?.let { AppText.get(R.string.memory_undo) },
                    duration = if (m.undo != null) SnackbarDuration.Long else SnackbarDuration.Short)
                if (r == SnackbarResult.ActionPerformed) m.undo?.let(vm::undoDelete)
            }
        }
    }
    val chat: ((String, Long?) -> Unit)? = openChat?.let { open ->
        { s: String, m: Long? -> scope.launch { open(s, m)?.let { snackbar.showSnackbar(it) } }; Unit }
    }
    val cb = MemoryCallbacks(
        back = onBack, retry = vm::load, setQuery = vm::setQuery, setKind = vm::setKind, learnNow = vm::learnNow,
        keep = vm::keep, dismiss = vm::dismiss,
        add = { editing = MemorySheetRequest() }, edit = { editing = MemorySheetRequest(it) },
        delete = vm::delete, pin = vm::pin, makeTask = vm::makeTask,
        openCard = { card = it }, openSource = openConversation, openChat = chat,
    )
    MemoryLayout(wide, card != null,
        page = { mod -> MemoryScreen(ui, assistantName, cb, snackbar, mod) },
        pane = {
            card?.let { id ->
                PersonCardHost(PersonTarget.Entry(id), store, assistantName, vip, onClose = { card = null }, openConversation, onChanged = vm::load,
                    openChat = chat)
            }
        })
    if (!wide) card?.let { id ->
        PersonCardSheet(PersonTarget.Entry(id), store, assistantName, vip, onClose = { card = null }, openConversation, onChanged = vm::load,
            openChat = chat)
    }
    editing?.let { req ->
        LaunchedEffect(Unit) {
            entities = try { store.entities() } catch (e: CancellationException) { throw e } catch (_: Exception) { emptyList() }
        }
        MemoryEditSheet(req, entities, assistantName, onDismiss = { editing = null }) { id, edit ->
            editing = null
            vm.save(id, edit)
        }
    }
}
