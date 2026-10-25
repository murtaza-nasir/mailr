package io.github.murtazanasir.mailr.ui.settings

import io.github.murtazanasir.mailr.util.catching

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
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.ForwardToInbox
import androidx.compose.material.icons.outlined.Inbox
import androidx.compose.material.icons.outlined.Mic
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
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.StandingOrder
import io.github.murtazanasir.mailr.data.api.setOrderEnabled
import io.github.murtazanasir.mailr.data.api.setOrderPermission
import io.github.murtazanasir.mailr.data.api.standingOrders
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import io.github.murtazanasir.mailr.look.ListItem
import io.github.murtazanasir.mailr.look.RadioButton
import io.github.murtazanasir.mailr.look.Switch
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/** What Settings → Ambrose → Standing orders reads and changes (GET/PATCH /assistant/orders). Tests pass a fake. */
interface OrdersStore {
    suspend fun list(): List<StandingOrder>
    suspend fun setEnabled(id: Long, enabled: Boolean)
    suspend fun setPermission(id: Long, permission: String)
}

class ApiOrdersStore(private val g: AppGraph) : OrdersStore {
    override suspend fun list() = g.api.standingOrders().items
    override suspend fun setEnabled(id: Long, enabled: Boolean) { g.api.setOrderEnabled(id, enabled) }
    override suspend fun setPermission(id: Long, permission: String) { g.api.setOrderPermission(id, permission) }
}

@Composable
fun OrdersRoute(g: AppGraph, assistantName: String, onBack: () -> Unit) {
    val store = remember { ApiOrdersStore(g) }
    OrdersPage(store, assistantName, onBack)
}

/** An order's name: the built-in order's own translated name, else the name the user gave it. */
@Composable
fun orderName(o: StandingOrder): String =
    if (o.builtin == "forwarded_notes") stringResource(R.string.orders_builtin_name) else o.name

/**
 * Settings → Ambrose → Standing orders on a phone: the orders
 * top first, each with its switch; the built-in order "Follow my forwarded notes" with what it does
 * and its permission (Ask me or Allow; Off is its switch). Creating, editing and reordering stay on
 * the web.
 */
@Composable
fun OrdersPage(store: OrdersStore, assistantName: String, onBack: () -> Unit) {
    var data by remember { mutableStateOf<List<StandingOrder>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

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
    fun run(block: suspend () -> Unit) = scope.launch {
        busy = true
        try {
            block()
            data = store.list()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            catching { data = store.list() }
            scope.launch { snackbar.showSnackbar(e.userMessage()) }
        } finally {
            busy = false
        }
    }
    LaunchedEffect(Unit) { load() }
    Scaffold(
        contentWindowInsets = WindowInsets(0),
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = { TopAppBar(title = { Text(stringResource(R.string.orders_title)) },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.settings_back)) } }) },
    ) { pad ->
        androidx.compose.runtime.CompositionLocalProvider(io.github.murtazanasir.mailr.look.LocalRowDividers provides true) {
            Box(Modifier.padding(pad).fillMaxSize(), contentAlignment = Alignment.TopCenter) {
                LazyColumn(Modifier.widthIn(max = 720.dp).fillMaxWidth().testTag("orders-list"), contentPadding = PaddingValues(bottom = 32.dp)) {
                    item {
                        Text(stringResource(R.string.orders_intro), style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp))
                    }
                    val list = data
                    when {
                        list == null && error != null -> item {
                            Column(Modifier.padding(horizontal = 20.dp, vertical = 12.dp)) {
                                Text(error.orEmpty(), color = MaterialTheme.colorScheme.error)
                                TextButton(onClick = { load() }) { Text(stringResource(R.string.settings_try_again)) }
                            }
                        }
                        list == null -> item { Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() } }
                        list.isEmpty() -> item {
                            Text(stringResource(R.string.orders_none), color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp))
                        }
                        else -> items(list, key = { it.id }) { o ->
                            OrderRow(o, busy, assistantName,
                                toggle = { on -> run { store.setEnabled(o.id, on) } },
                                permission = { p -> run { store.setPermission(o.id, p) } })
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun OrderRow(o: StandingOrder, busy: Boolean, assistantName: String, toggle: (Boolean) -> Unit, permission: (String) -> Unit) {
    val name = orderName(o)
    val builtin = o.builtin == "forwarded_notes"
    val kind = when {
        builtin -> stringResource(R.string.orders_builtin)
        o.trigger == "schedule" -> stringResource(R.string.orders_when_routine)
        o.trigger == "recording" -> stringResource(R.string.orders_when_recording)
        o.query.isNotBlank() -> stringResource(R.string.orders_mail_query, o.query)
        else -> stringResource(R.string.orders_when_mail)
    }
    val state = if (o.enabled) AppText.get(R.string.orders_on, name) else AppText.get(R.string.orders_off_label, name)
    Column(Modifier.testTag(if (builtin) "order-builtin" else "order")) {
        ListItem(headlineContent = { Text(name) },
            supportingContent = { Text(if (builtin) "$kind\n${AppText.get(R.string.orders_builtin_desc, assistantName)}" else kind) },
            leadingContent = {
                Icon(when {
                    builtin -> Icons.Outlined.ForwardToInbox
                    o.trigger == "schedule" -> Icons.Outlined.CalendarMonth
                    o.trigger == "recording" -> Icons.Outlined.Mic
                    else -> Icons.Outlined.Inbox
                }, null)
            },
            trailingContent = {
                Box(Modifier.testTag("order-switch").semantics { contentDescription = state }) {
                    Switch(o.enabled, { if (!busy) toggle(it) })
                }
            },
            modifier = Modifier.testTag("order-row").clickable(enabled = !busy) { toggle(!o.enabled) })
        if (builtin) {
            if (!o.enabled) {
                Text(stringResource(R.string.orders_off), style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(start = 56.dp, end = 20.dp, bottom = 8.dp))
            } else {
                val current = o.permission ?: "ask"
                listOf(
                    Triple("ask", R.string.orders_permission_ask, AppText.get(R.string.orders_permission_ask_desc, assistantName)),
                    Triple("allow", R.string.orders_permission_allow, AppText.get(R.string.orders_permission_allow_desc, assistantName)),
                ).forEach { (p, title, desc) ->
                    val here = current == p
                    ListItem(headlineContent = { Text(stringResource(title)) }, supportingContent = { Text(desc) },
                        leadingContent = { RadioButton(here, onClick = null) },
                        modifier = Modifier.padding(start = 40.dp).testTag("order-permission-$p")
                            .clickable(enabled = !busy, role = Role.RadioButton) { if (!here) permission(p) })
                }
            }
        }
    }
}
