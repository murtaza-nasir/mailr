package io.github.murtazanasir.mailr.ui.settings

import io.github.murtazanasir.mailr.util.catching

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.Rule
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Mail
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
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.TrustedAddress
import io.github.murtazanasir.mailr.data.api.TrustedListing
import io.github.murtazanasir.mailr.data.api.addTrusted
import io.github.murtazanasir.mailr.data.api.confirmTrusted
import io.github.murtazanasir.mailr.data.api.removeTrusted
import io.github.murtazanasir.mailr.data.api.resendTrusted
import io.github.murtazanasir.mailr.data.api.setHandledElsewhere
import io.github.murtazanasir.mailr.data.api.trustedAddresses
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import io.github.murtazanasir.mailr.look.FilledTonalButton
import io.github.murtazanasir.mailr.look.ListItem
import io.github.murtazanasir.mailr.look.OutlinedTextField
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/**
 * What Settings → Ambrose → Trusted addresses reads and changes (GET/POST/DELETE
 * /assistant/trusted-addresses; the setting handled_elsewhere). Tests pass a fake.
 */
interface TrustedStore {
    suspend fun list(): TrustedListing
    suspend fun add(email: String): TrustedAddress
    suspend fun confirm(id: Long, code: String): TrustedAddress
    suspend fun resend(id: Long): TrustedAddress
    suspend fun remove(id: Long)
    suspend fun setElsewhere(addresses: List<String>)
}

class ApiTrustedStore(private val g: AppGraph) : TrustedStore {
    override suspend fun list() = g.api.trustedAddresses()
    override suspend fun add(email: String) = g.api.addTrusted(email)
    override suspend fun confirm(id: Long, code: String) = g.api.confirmTrusted(id, code)
    override suspend fun resend(id: Long) = g.api.resendTrusted(id)
    override suspend fun remove(id: Long) { g.api.removeTrusted(id) }
    override suspend fun setElsewhere(addresses: List<String>) { g.api.setHandledElsewhere(addresses) }
}

@Composable
fun TrustedRoute(g: AppGraph, assistantName: String, openOrders: () -> Unit, onBack: () -> Unit) {
    val store = remember { ApiTrustedStore(g) }
    TrustedPage(store, assistantName, onBack, openOrders)
}

/**
 * Settings → Ambrose → Trusted addresses, as on the
 * web: the addresses with their state, a code field for one that waits, Send a new code, Remove, an
 * address to add, the addresses that look like yours, and the addresses another assistant handles.
 * The identity page only: what Ambrose does with a forwarded note is the built-in standing order
 * (Standing orders, one tap away).
 */
@Composable
fun TrustedPage(store: TrustedStore, assistantName: String, onBack: () -> Unit, openOrders: () -> Unit = {}) {
    var data by remember { mutableStateOf<TrustedListing?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var removing by remember { mutableStateOf<TrustedAddress?>(null) }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    fun run(block: suspend () -> String?) = scope.launch {
        busy = true
        try {
            val msg = block()
            data = store.list()
            error = null
            // The snackbar waits on its own: the buttons work again at once.
            msg?.let { scope.launch { snackbar.showSnackbar(it) } }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            catching { data = store.list() }
            scope.launch { snackbar.showSnackbar(e.userMessage()) }
        } finally {
            busy = false
        }
    }
    fun load() = scope.launch {
        try {
            data = store.list()
            error = null
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            error = e.userMessage()
        }
    }
    LaunchedEffect(Unit) { load() }
    val minutes = data?.codeMinutes ?: 30
    val cb = TrustedCallbacks(
        add = { email -> run { store.add(email); AppText.plural(R.plurals.trusted_code_sent, minutes, email, minutes) } },
        confirm = { a, code -> run { store.confirm(a.id, code); AppText.get(R.string.trusted_confirmed_msg, a.email) } },
        resend = { a -> run { store.resend(a.id); AppText.plural(R.plurals.trusted_code_sent, minutes, a.email, minutes) } },
        remove = { a -> removing = a },
        addElsewhere = { a ->
            val list = data?.handledElsewhere.orEmpty()
            val email = a.trim().lowercase()
            if (email.isNotEmpty() && email !in list) run { store.setElsewhere(list + email); AppText.get(R.string.trusted_elsewhere_added, email) }
        },
        removeElsewhere = { a ->
            run { store.setElsewhere(data?.handledElsewhere.orEmpty() - a); AppText.get(R.string.trusted_elsewhere_removed, a) }
        },
        openOrders = openOrders,
        retry = { load() },
    )
    TrustedScreen(data, error, busy, assistantName, cb, snackbar, onBack)
    removing?.let { a ->
        AlertDialog(
            onDismissRequest = { removing = null },
            title = { Text(stringResource(R.string.trusted_remove_title, a.email)) },
            text = { Text(stringResource(R.string.trusted_remove_body)) },
            confirmButton = {
                TextButton(onClick = {
                    removing = null
                    run { store.remove(a.id); AppText.get(R.string.trusted_removed_msg, a.email) }
                }, modifier = Modifier.testTag("trusted-remove-confirm")) { Text(stringResource(R.string.trusted_remove)) }
            },
            dismissButton = { TextButton(onClick = { removing = null }) { Text(stringResource(R.string.trusted_cancel)) } },
        )
    }
}

class TrustedCallbacks(
    val add: (String) -> Unit,
    val confirm: (TrustedAddress, String) -> Unit,
    val resend: (TrustedAddress) -> Unit,
    val remove: (TrustedAddress) -> Unit,
    val addElsewhere: (String) -> Unit,
    val removeElsewhere: (String) -> Unit,
    val openOrders: () -> Unit,
    val retry: () -> Unit,
)

@Composable
fun TrustedScreen(data: TrustedListing?, error: String?, busy: Boolean, assistantName: String, cb: TrustedCallbacks,
                  snackbar: SnackbarHostState = remember { SnackbarHostState() }, onBack: () -> Unit) {
    var email by remember { mutableStateOf("") }
    var other by remember { mutableStateOf("") }
    val codes = remember { mutableStateMapOf<Long, String>() }
    Scaffold(
        contentWindowInsets = WindowInsets(0),
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = { TopAppBar(title = { Text(stringResource(R.string.trusted_title)) },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.settings_back)) } }) },
    ) { pad ->
        androidx.compose.runtime.CompositionLocalProvider(io.github.murtazanasir.mailr.look.LocalRowDividers provides true) {
            Box(Modifier.padding(pad).fillMaxSize(), contentAlignment = Alignment.TopCenter) {
                LazyColumn(Modifier.widthIn(max = 720.dp).fillMaxWidth().testTag("trusted-list"), contentPadding = PaddingValues(bottom = 32.dp)) {
                    item {
                        Text(stringResource(R.string.trusted_intro, assistantName), style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp))
                    }
                    when {
                        data == null && error != null -> item {
                            Column(Modifier.padding(horizontal = 20.dp, vertical = 12.dp)) {
                                Text(error, color = MaterialTheme.colorScheme.error)
                                TextButton(onClick = cb.retry) { Text(stringResource(R.string.settings_try_again)) }
                            }
                        }
                        data == null -> item { Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() } }
                        else -> {
                            if (data.addresses.isEmpty()) item {
                                Text(stringResource(R.string.trusted_none), color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp))
                            }
                            items(data.addresses, key = { it.id }) { a ->
                                AddressRow(a, codes[a.id].orEmpty(), busy, { codes[a.id] = it }, cb)
                            }
                            item {
                                Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    OutlinedTextField(email, { email = it }, Modifier.weight(1f).testTag("trusted-add-email"), singleLine = true,
                                        label = { Text(stringResource(R.string.trusted_address_of_yours)) },
                                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email))
                                    FilledTonalButton(onClick = { cb.add(email.trim()); email = "" }, enabled = email.isNotBlank() && !busy,
                                        modifier = Modifier.testTag("trusted-add")) { Text(stringResource(R.string.trusted_send_code)) }
                                }
                                Text(pluralStringResource(R.plurals.trusted_how, data.codeMinutes, data.codeMinutes), style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 20.dp))
                            }
                            if (data.suggestions.isNotEmpty()) {
                                item { Header(stringResource(R.string.trusted_look_like_yours)) }
                                items(data.suggestions, key = { "s-" + it.email }) { s ->
                                    ListItem(headlineContent = { Text(s.email) },
                                        supportingContent = { Text(pluralStringResource(R.plurals.trusted_messages_from, s.messages, s.messages)) },
                                        trailingContent = { TextButton(onClick = { cb.add(s.email) }, enabled = !busy) { Text(stringResource(R.string.trusted_send_code)) } },
                                        modifier = Modifier.testTag("trusted-suggestion"))
                                }
                            }
                            item {
                                ListItem(headlineContent = { Text(stringResource(R.string.trusted_open_orders)) },
                                    supportingContent = { Text(stringResource(R.string.trusted_follow_order, assistantName)) },
                                    leadingContent = { Icon(Icons.AutoMirrored.Outlined.Rule, null) },
                                    modifier = Modifier.testTag("trusted-open-orders").clickable { cb.openOrders() })
                            }
                            item { Header(stringResource(R.string.trusted_elsewhere_title)) }
                            item {
                                Text(stringResource(R.string.trusted_elsewhere_desc), style = MaterialTheme.typography.bodyMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp))
                            }
                            if (data.handledElsewhere.isEmpty()) item {
                                Text(stringResource(R.string.trusted_elsewhere_none), color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp))
                            }
                            items(data.handledElsewhere, key = { "e-$it" }) { a ->
                                ListItem(headlineContent = { Text(a) }, leadingContent = { Icon(Icons.Outlined.Mail, null) },
                                    trailingContent = {
                                        IconButton(onClick = { cb.removeElsewhere(a) }, enabled = !busy, modifier = Modifier.testTag("elsewhere-remove")) {
                                            Icon(Icons.Outlined.Delete, stringResource(R.string.trusted_elsewhere_remove, a))
                                        }
                                    }, modifier = Modifier.testTag("elsewhere-address"))
                            }
                            item {
                                Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    OutlinedTextField(other, { other = it }, Modifier.weight(1f).testTag("elsewhere-email"), singleLine = true,
                                        label = { Text(stringResource(R.string.trusted_elsewhere_address)) },
                                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email))
                                    FilledTonalButton(onClick = { cb.addElsewhere(other); other = "" }, enabled = other.isNotBlank() && !busy,
                                        modifier = Modifier.testTag("elsewhere-add")) { Text(stringResource(R.string.trusted_elsewhere_add)) }
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
private fun AddressRow(a: TrustedAddress, code: String, busy: Boolean, setCode: (String) -> Unit, cb: TrustedCallbacks) {
    val state = stringResource(when (a.state) {
        "confirmed" -> R.string.trusted_state_confirmed
        "pending" -> R.string.trusted_state_pending
        else -> R.string.trusted_state_expired
    })
    val last = a.lastMessage?.let { stringResource(if (it.verified) R.string.trusted_last_verified else R.string.trusted_last_not_verified) }
    Column(Modifier.testTag("trusted-address")) {
        ListItem(headlineContent = { Text(a.email) },
            supportingContent = { Text(if (last != null) "$state\n$last" else state) },
            leadingContent = { Icon(Icons.Outlined.Mail, null) },
            trailingContent = {
                IconButton(onClick = { cb.remove(a) }, modifier = Modifier.testTag("trusted-remove")) {
                    Icon(Icons.Outlined.Delete, stringResource(R.string.trusted_remove_address, a.email))
                }
            })
        if (!a.confirmed) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(code, setCode, Modifier.weight(1f).testTag("trusted-code"), singleLine = true,
                    enabled = a.state != "expired", label = { Text(stringResource(R.string.trusted_code)) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword))
                FilledTonalButton(onClick = { cb.confirm(a, code.trim()); setCode("") }, enabled = code.isNotBlank() && !busy && a.state != "expired",
                    modifier = Modifier.testTag("trusted-confirm")) { Text(stringResource(R.string.trusted_confirm)) }
            }
            TextButton(onClick = { cb.resend(a) }, enabled = !busy, modifier = Modifier.padding(horizontal = 8.dp).testTag("trusted-resend")) {
                Text(stringResource(R.string.trusted_resend))
            }
        }
    }
}
