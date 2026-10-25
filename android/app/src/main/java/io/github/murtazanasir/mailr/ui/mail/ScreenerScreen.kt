package io.github.murtazanasir.mailr.ui.mail

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.HowToReg
import io.github.murtazanasir.mailr.look.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedButton
import io.github.murtazanasir.mailr.look.OutlinedCard
import androidx.compose.material3.Scaffold
import io.github.murtazanasir.mailr.look.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.ScreenerList
import io.github.murtazanasir.mailr.data.api.ScreenerSender
import io.github.murtazanasir.mailr.ui.components.ConversationSkeleton
import io.github.murtazanasir.mailr.ui.components.EmptyState
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R

/**
 * The Screener on the phone (android.screener; backend core/screener.py): the first-time senders
 * waiting, with Allow and Block. Labels, Block to Trash and the settings are on the web.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ScreenerScreen(
    list: ScreenerList?,
    error: String?,
    refreshing: Boolean,
    snackbar: SnackbarHostState,
    onBack: () -> Unit,
    onRefresh: () -> Unit,
    onDecide: (ScreenerSender, String) -> Unit,
    onOpen: (ScreenerSender) -> Unit,
) {
    Scaffold(
        contentWindowInsets = WindowInsets(0),
        topBar = {
            TopAppBar(title = { Text(stringResource(R.string.mail_screener)) },
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.mail_back)) } })
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { pad ->
        PullToRefreshBox(refreshing, onRefresh, Modifier.padding(pad).fillMaxSize()) {
            when {
                list == null && error != null -> EmptyState(Icons.Outlined.HowToReg, stringResource(R.string.mail_screener_cant_load), error,
                    action = stringResource(R.string.mail_try_again) to onRefresh)
                list == null -> ConversationSkeleton(3)
                list.senders.isEmpty() -> EmptyState(Icons.Outlined.HowToReg,
                    if (list.enabled) stringResource(R.string.mail_screener_no_senders) else stringResource(R.string.mail_screener_off),
                    if (list.enabled) stringResource(R.string.mail_screener_no_senders_body)
                    else stringResource(R.string.mail_screener_off_body))
                else -> LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    items(list.senders, key = { it.id }) { s -> SenderCard(s, onDecide, onOpen) }
                }
            }
        }
    }
}

@Composable
private fun SenderCard(s: ScreenerSender, onDecide: (ScreenerSender, String) -> Unit, onOpen: (ScreenerSender) -> Unit) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    OutlinedCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(s.title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold,
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.semantics { heading() })
            Text(s.email, style = MaterialTheme.typography.bodySmall, color = muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(listOf(s.scope, s.authLabel, s.held).joinToString(" · "), style = MaterialTheme.typography.labelMedium,
                color = if (s.auth.level == "failed") MaterialTheme.colorScheme.error else muted)
            Column(Modifier.fillMaxWidth().clickable(onClickLabel = stringResource(R.string.mail_screener_read)) { onOpen(s) }.padding(vertical = 4.dp)) {
                Text(s.latest.subject.ifBlank { stringResource(R.string.mail_no_subject) }, style = MaterialTheme.typography.bodyMedium,
                    fontWeight = if (s.unread > 0) FontWeight.SemiBold else FontWeight.Normal, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(s.latest.snippet, style = MaterialTheme.typography.bodySmall, color = muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                // ACT screener.allow @android.screener
                Button(onClick = { onDecide(s, "allow") }) { Text(stringResource(R.string.mail_screener_allow)) }
                // ACT screener.block @android.screener
                OutlinedButton(onClick = { onDecide(s, "block") }) { Text(stringResource(R.string.mail_screener_block)) }
            }
        }
    }
}
