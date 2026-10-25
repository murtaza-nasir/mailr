package io.github.murtazanasir.mailr.ui.mail

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.CloudUpload
import androidx.compose.material.icons.outlined.EditNote
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Lock
import io.github.murtazanasir.mailr.look.AlertDialog
import androidx.compose.material3.Icon
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.db.LocalDraftEntity
import io.github.murtazanasir.mailr.data.db.PendingOpEntity
import io.github.murtazanasir.mailr.data.db.PendingState
import io.github.murtazanasir.mailr.data.repo.PendingScopes
import io.github.murtazanasir.mailr.ui.components.refuseObscuredTouches
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/**
 * One line of Pending changes: a queued or failed change, or a composer's text that only
 * this phone has.
 */
data class PendingChange(
    val key: String,
    val title: String,
    val status: String,
    val failed: Boolean,
    val opId: Long? = null,
    val localDraftId: String? = null,
) {
    val isDraft: Boolean get() = localDraftId != null

    companion object {
        fun of(op: PendingOpEntity, paused: Boolean): PendingChange {
            val failed = op.state == PendingState.FAILED
            val status = when {
                failed -> PendingScopes.failure(op.lastError)
                paused -> AppText.get(R.string.mail_pending_signin)
                op.attempts > 0 -> op.lastError?.let { AppText.get(R.string.mail_pending_retrying_error, it) } ?: AppText.get(R.string.mail_pending_retrying)
                else -> AppText.get(R.string.mail_pending_waiting)
            }
            return PendingChange("op-${op.id}", PendingScopes.title(op), status, failed, opId = op.id)
        }

        fun of(d: LocalDraftEntity): PendingChange = PendingChange(
            "draft-${d.id}", AppText.get(R.string.mail_pending_draft, d.subject.ifBlank { AppText.get(R.string.mail_no_subject) }),
            if (d.error != null) AppText.get(R.string.mail_pending_draft_failed, d.error) else AppText.get(R.string.mail_pending_draft_saved),
            failed = d.error != null, localDraftId = d.id)

        /** Failed first (they need the user), then the queue in its order, then drafts. */
        fun list(ops: List<PendingOpEntity>, drafts: List<LocalDraftEntity>, paused: Boolean): List<PendingChange> {
            val fromOps = ops.map { of(it, paused) }
            val fromDrafts = drafts.filter { !it.synced }.map { of(it) }
            return (fromOps + fromDrafts).sortedBy { if (it.failed) 0 else 1 }
        }
    }
}

data class PendingCallbacks(
    val retry: (Long) -> Unit,
    val discard: (Long) -> Unit,
    val openDraft: (String) -> Unit,
    val discardDraft: (String) -> Unit,
    val dismiss: () -> Unit,
)

/** The banner over the list while anything waits to sync; a tap opens Pending changes. */
/** Above cached mail when a refresh failed (offline, or the server is down): the list is not current. */
@Composable
fun StaleBanner(onRetry: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    val (bg, fg) = io.github.murtazanasir.mailr.look.bannerColors(io.github.murtazanasir.mailr.look.BannerTone.Warning,
        cs.tertiaryContainer, cs.onTertiaryContainer)
    Surface(color = bg, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("stale-banner")
        .clickable(onClickLabel = stringResource(R.string.mail_try_again), onClick = onRetry)) {
        Row(Modifier.padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.CloudOff, null, Modifier.size(18.dp), tint = fg)
            Spacer(Modifier.width(12.dp))
            Text(stringResource(R.string.mail_offline_showing), style = MaterialTheme.typography.bodySmall, color = fg, modifier = Modifier.weight(1f))
            Text(stringResource(R.string.mail_try_again), style = MaterialTheme.typography.labelLarge, color = fg)
        }
    }
}

@Composable
fun PendingBanner(waiting: Int, failed: Int, onClick: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    // The web's tints in its look: danger when something failed, warning while changes wait.
    val (bg, fg) = io.github.murtazanasir.mailr.look.bannerColors(if (failed > 0) io.github.murtazanasir.mailr.look.BannerTone.Danger else io.github.murtazanasir.mailr.look.BannerTone.Warning,
        if (failed > 0) cs.errorContainer else cs.tertiaryContainer, if (failed > 0) cs.onErrorContainer else cs.onTertiaryContainer)
    val text = when {
        failed > 0 -> pluralStringResource(R.plurals.mail_pending_failed, failed, failed) +
            (if (waiting > 0) stringResource(R.string.mail_pending_waiting_suffix, waiting) else "")
        else -> pluralStringResource(R.plurals.mail_pending_offline, waiting, waiting)
    }
    Surface(color = bg, modifier = Modifier.fillMaxWidth().clickable(onClickLabel = stringResource(R.string.mail_pending_show), onClick = onClick)) {
        Row(Modifier.padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(if (failed > 0) Icons.Outlined.ErrorOutline else Icons.Outlined.CloudOff, null, Modifier.size(18.dp), tint = fg)
            Spacer(Modifier.width(12.dp))
            Text(text, style = MaterialTheme.typography.bodySmall, color = fg, modifier = Modifier.weight(1f))
            Text(stringResource(R.string.mail_details), style = MaterialTheme.typography.labelLarge, color = fg)
        }
    }
}

@Composable
fun PendingChangesSheet(items: List<PendingChange>, paused: Boolean, cb: PendingCallbacks) {
    val state = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(onDismissRequest = cb.dismiss, sheetState = state) {
        PendingChangesContent(items, paused, cb)
    }
}

/**
 * Pending changes: each change with what is happening to it. Retry puts a failed change back in
 * the queue; Discard forgets it (the lists then show the server's state). A draft opens in the
 * composer, or is discarded after a confirmation.
 */
@Composable
fun PendingChangesContent(items: List<PendingChange>, paused: Boolean, cb: PendingCallbacks) {
    var confirmDraft by remember { mutableStateOf<PendingChange?>(null) }
    Column(Modifier.navigationBarsPadding().padding(bottom = 12.dp).testTag("pending-changes")) {
        Text(stringResource(R.string.mail_pending_title), style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.padding(horizontal = 24.dp, vertical = 8.dp).semantics { heading() })
        if (paused) Row(Modifier.padding(horizontal = 24.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.Lock, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.width(8.dp))
            Text(stringResource(R.string.mail_pending_session_ended),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (items.isEmpty()) Text(stringResource(R.string.mail_pending_in_sync), style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 24.dp, vertical = 16.dp))
        LazyColumn(Modifier.weight(1f, fill = false)) {
            items(items, key = { it.key }) { c ->
                ListItem(
                    headlineContent = { Text(c.title) },
                    supportingContent = {
                        Text(c.status, color = if (c.failed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
                    },
                    leadingContent = {
                        Icon(when {
                            c.isDraft -> Icons.Outlined.EditNote
                            c.failed -> Icons.Outlined.ErrorOutline
                            else -> Icons.Outlined.CloudUpload
                        }, null, tint = if (c.failed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
                    },
                    colors = ListItemDefaults.colors(containerColor = Color.Transparent),
                )
                Row(Modifier.fillMaxWidth().padding(start = 56.dp, end = 16.dp), horizontalArrangement = Arrangement.End) {
                    if (c.isDraft) {
                        TextButton(onClick = { confirmDraft = c }) { Text(stringResource(R.string.mail_discard)) }
                        TextButton(onClick = { cb.openDraft(c.localDraftId!!) }) { Text(stringResource(R.string.mail_open)) }
                    } else {
                        TextButton(onClick = { cb.discard(c.opId!!) }) { Text(stringResource(R.string.mail_discard)) }
                        if (c.failed) TextButton(onClick = { cb.retry(c.opId!!) }) { Text(stringResource(R.string.mail_retry)) }
                    }
                }
            }
        }
    }
    confirmDraft?.let { c ->
        AlertDialog(
            onDismissRequest = { confirmDraft = null },
            title = { Text(stringResource(R.string.mail_discard_draft_title)) },
            text = { Text(stringResource(R.string.mail_discard_draft_text)) },
            confirmButton = {
                TextButton(onClick = { confirmDraft = null; cb.discardDraft(c.localDraftId!!) }, modifier = Modifier.refuseObscuredTouches()) {
                    Text(stringResource(R.string.mail_discard), color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = { TextButton(onClick = { confirmDraft = null }) { Text(stringResource(R.string.mail_cancel)) } },
        )
    }
}
