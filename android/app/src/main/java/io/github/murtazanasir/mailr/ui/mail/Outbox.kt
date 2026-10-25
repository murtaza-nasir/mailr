package io.github.murtazanasir.mailr.ui.mail

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.OutboxRow
import io.github.murtazanasir.mailr.data.api.inOutbox
import io.github.murtazanasir.mailr.data.api.notSent
import io.github.murtazanasir.mailr.ui.conversation.plainSendError
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/** The drawer's Outbox entry (shown only while something is queued or failed). */
data class OutboxCount(val total: Int = 0, val failed: Int = 0) {
    companion object {
        fun of(rows: List<OutboxRow>): OutboxCount = rows.filter { it.inOutbox() }.let { r -> OutboxCount(r.size, r.count { it.notSent() }) }
    }
}

/** One row's state in words. */
fun outboxLine(o: OutboxRow): String = when (o.status) {
    "failed" -> AppText.get(R.string.mail_outbox_not_sent, plainSendError(o.error))
    "uncertain" -> AppText.get(R.string.mail_outbox_maybe_sent, plainSendError(o.error))
    "sending" -> AppText.get(R.string.mail_outbox_sending)
    else -> AppText.get(R.string.mail_outbox_waiting)
}

/**
 * ACT draft.outbox @android.drawer: what has not left yet, and what was not sent, each with Retry
 * (ACT draft.retry_send @android.drawer) or Open (the conversation).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun OutboxSheet(rows: List<OutboxRow>, onDismiss: () -> Unit, onRetry: (OutboxRow) -> Unit, onOpen: (OutboxRow) -> Unit) {
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.navigationBarsPadding().testTag("outbox-sheet")) {
            Text(stringResource(R.string.mail_outbox), style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(horizontal = 24.dp).semantics { heading() })
            val shown = rows.filter { it.inOutbox() }
            LazyColumn(Modifier.heightIn(min = 96.dp, max = 460.dp)) {
                if (shown.isEmpty()) item { Text(stringResource(R.string.mail_outbox_empty), Modifier.padding(24.dp), color = MaterialTheme.colorScheme.onSurfaceVariant) }
                items(shown, key = { it.id }) { o ->
                    val failed = o.notSent()
                    ListItem(
                        headlineContent = { Text(o.subject.ifBlank { stringResource(R.string.mail_no_subject) }, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                        supportingContent = {
                            Column {
                                if (o.recipients.isNotEmpty()) Text(stringResource(R.string.mail_outbox_to, o.recipients.joinToString()), maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text(outboxLine(o), color = if (failed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        },
                        leadingContent = { Icon(if (failed) Icons.Outlined.ErrorOutline else Icons.Outlined.Schedule, null,
                            tint = if (failed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant) },
                        trailingContent = {
                            Row {
                                if (failed) TextButton(onClick = { onRetry(o) }) { Text(stringResource(R.string.mail_retry)) }
                                if (o.conversationId != null) TextButton(onClick = { onOpen(o) }) { Text(stringResource(R.string.mail_open)) }
                            }
                        },
                    )
                }
            }
        }
    }
}
