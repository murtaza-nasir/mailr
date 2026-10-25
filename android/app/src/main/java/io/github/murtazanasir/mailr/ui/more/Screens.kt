package io.github.murtazanasir.mailr.ui.more

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.ui.components.refuseObscuredTouches
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.ui.platform.testTag
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.EventAvailable
import androidx.compose.material.icons.outlined.RadioButtonUnchecked
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material.icons.outlined.VerifiedUser
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.Card
import androidx.compose.material3.CardDefaults
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedButton
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.PrimaryTabRow
import androidx.compose.material3.Scaffold
import io.github.murtazanasir.mailr.look.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.ui.components.ConversationSkeleton
import io.github.murtazanasir.mailr.ui.components.EmptyState
import io.github.murtazanasir.mailr.ui.theme.Mail
import io.github.murtazanasir.mailr.ui.util.Format
import java.time.LocalDate
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

// ======================= approvals =======================

// The Approvals screen is in ui/approvals/ApprovalsUi.kt. ApprovalCard is one pending approval, on
// that screen and in the assistant's chat.

/**
 * One approval: the server's display model (ui/present/ApprovalBody.kt; older servers send title and
 * lines), the decision buttons while it waits, and once decided its outcome in place ("Approved ·
 * moved 6 messages to Trash") with the details folded away. ACT approval.approve @android.approvals @android.assistant, ACT approval.deny @android.approvals @android.assistant
 *
 * [onAsk] adds Ask to the header: the ask capture with this approval as its chip, so the assistant
 * can explain it. Only the buttons here decide it. ACT agent.ask_about @android.assistant @android.approvals
 */
/** A grant in the card's Allow… menu: its label, and under it what it covers. */
@Composable
private fun GrantItem(label: String, help: String) {
    Column(Modifier.widthIn(max = 320.dp).padding(vertical = 4.dp)) {
        Text(label, style = MaterialTheme.typography.bodyLarge)
        Text(help, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/**
 * A review card (`preview.kind == "review"`, ACT agent.review_mail @android.assistant) shows its plan by
 * sender with a move menu per sender, conversation and kept or unsure row, and only Apply and Discard,
 * with "N changed by you" beside them. [onApplyReview] approves it once with the user's moves (`changes`,
 * by conversation id); without it the menus cannot change and Apply approves the whole card.
 * [reviewAlways]: a review card's "Always …" per sender (in the chat); null shows none.
 */
@Composable
fun ApprovalCard(a: Approval, onAsk: ((Approval) -> Unit)? = null, onApplyReview: ((Approval, Map<Long, String>) -> Unit)? = null,
                 reviewAlways: io.github.murtazanasir.mailr.ui.assistant.ReviewAlways? = null,
                 onDecide: (Approval, Boolean, String) -> Unit) {
    var more by remember { mutableStateOf(false) }
    var details by remember { mutableStateOf(false) }
    val d = a.shown
    val review = a.review
    // A review card: the user's own moves on it, by conversation id, sent with Apply (apply_changes).
    var changes by androidx.compose.runtime.saveable.rememberSaveable(a.id) { mutableStateOf(emptyMap<Long, String>()) }
    val pending = a.status == "pending"
    val outcome = a.outcome?.takeIf { it.state == a.status }?.localized() ?: io.github.murtazanasir.mailr.ui.present.localOutcome(a.status)
    // A pending card (on the Approvals screen or in the assistant chat) hides other apps' overlays,
    // so none can cover its text while the buttons stay clear.
    if (pending) io.github.murtazanasir.mailr.ui.components.SensitiveScreen()
    val neededDesc = stringResource(R.string.more_approval_needed_desc, d?.title ?: a.preview.title)
    val outcomeDesc = stringResource(R.string.more_approval_outcome_desc, d?.title ?: a.preview.title, outcome.text)
    Card(Modifier.widthIn(max = 640.dp).fillMaxWidth().semantics { contentDescription = if (pending) neededDesc else outcomeDesc },
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
        border = if (pending) androidx.compose.foundation.BorderStroke(1.dp,
            if (d?.danger == true) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary) else null) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Surface(shape = CircleShape, color = MaterialTheme.colorScheme.tertiaryContainer, modifier = Modifier.size(36.dp)) {
                    Box(contentAlignment = Alignment.Center) {
                        Icon(io.github.murtazanasir.mailr.ui.present.displayIcon(d?.icon), null, Modifier.size(20.dp))
                    }
                }
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(d?.title ?: a.preview.title.ifBlank { a.action }, style = MaterialTheme.typography.titleMedium)
                    Text(a.actor.ifBlank { stringResource(R.string.more_an_agent) }, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                // Not on a legacy outbox row: its id is not a pending action's.
                val askDesc = stringResource(R.string.more_ask_about_approval, io.github.murtazanasir.mailr.ui.assistantName())
                if (onAsk != null && a.outboxId == null) androidx.compose.material3.TextButton(onClick = { onAsk(a) },
                    contentPadding = PaddingValues(horizontal = 10.dp),
                    modifier = Modifier.testTag("approval-ask").semantics { contentDescription = askDesc }) {
                    Icon(Icons.Outlined.AutoAwesome, null, Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text(stringResource(R.string.more_ask_ambrose, io.github.murtazanasir.mailr.ui.assistantName()))
                }
            }
            if (!pending) {
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(outcome.text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f),
                        color = when (a.status) {
                            "executed" -> MaterialTheme.colorScheme.primary
                            "denied", "failed", "expired" -> MaterialTheme.colorScheme.error
                            else -> MaterialTheme.colorScheme.onSurfaceVariant
                        })
                    if ((d != null && d.fields.isNotEmpty()) || review != null) androidx.compose.material3.TextButton(onClick = { details = !details }) { Text(stringResource(if (details) R.string.more_hide_details else R.string.more_details)) }
                }
            }
            if (pending || details) {
                if (review != null) {
                    Spacer(Modifier.height(12.dp))
                    if (d?.danger == true && pending) Text(stringResource(R.string.more_cant_undo), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(bottom = 6.dp))
                    io.github.murtazanasir.mailr.ui.assistant.ReviewPlan(review, changes, onChanges = { changes = it },
                        editable = pending && onApplyReview != null, always = reviewAlways)
                } else if (d != null) {
                    Spacer(Modifier.height(12.dp))
                    if (d.danger && pending) Text(stringResource(R.string.more_cant_undo), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(bottom = 6.dp))
                    io.github.murtazanasir.mailr.ui.present.ApprovalBody(d)
                } else if (a.preview.lines.isNotEmpty() || a.target.isNotBlank()) {
                    Spacer(Modifier.height(12.dp))
                    Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh,
                        modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp)) {
                            (a.preview.lines.ifEmpty { listOf(a.target) }).forEach {
                                Text(it, style = MaterialTheme.typography.bodyMedium)
                            }
                        }
                    }
                }
            }
            if (!pending) return@Column
            a.expiresAt?.let { Format.parse(it) }?.let {
                Text(stringResource(R.string.more_expires_in, Format.relative(AppClock.now(), it, ago = false)),
                    style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp))
            }
            Spacer(Modifier.height(12.dp))
            // A review card: only Apply and Discard, with how many conversations the user moved, as on the web.
            if (review != null) {
                Row(Modifier.refuseObscuredTouches(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    OutlinedButton(onClick = { onDecide(a, false, "once") }, modifier = Modifier.testTag("review-discard")) {
                        Text(stringResource(R.string.asst_review_discard))
                    }
                    Spacer(Modifier.weight(1f))
                    if (changes.isNotEmpty()) Text(pluralStringResource(R.plurals.asst_review_changed_by_you, changes.size,
                        java.text.NumberFormat.getIntegerInstance().format(changes.size)), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.testTag("review-changed"))
                    Button(onClick = { onApplyReview?.invoke(a, changes) ?: onDecide(a, true, "once") }, modifier = Modifier.testTag("review-apply")) {
                        Text(stringResource(R.string.asst_review_apply))
                    }
                }
                return@Column
            }
            // The answer buttons drop touches that come through another app's overlay (tapjacking).
            Row(Modifier.refuseObscuredTouches(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedButton(onClick = { onDecide(a, false, "once") }) { Text(stringResource(R.string.more_deny)) }
                Spacer(Modifier.weight(1f))
                // A card a hard rule held (grants false) is decided once: no ⋮ with grants, as on the web.
                if (a.outboxId == null && a.grants) Box {
                    // The rarer, longer grants behind ⋮, as on the web; its name says what is inside.
                    IconButton(onClick = { more = true }, modifier = Modifier.testTag("approval-more")) {
                        Icon(Icons.Outlined.MoreVert, stringResource(R.string.more_allow_menu))
                    }
                    // Each grant says what it covers, and how many other waiting requests it approves too
                    // (io.github.murtazanasir.mailr.ui.approvals.GrantText).
                    val grant = io.github.murtazanasir.mailr.ui.approvals.GrantText
                    DropdownMenu(more, { more = false }) {
                        DropdownMenuItem({ GrantItem(grant.sessionLabel(a) + grant.also(a.alsoCovers?.session), grant.sessionHelp(a)) },
                            { more = false; onDecide(a, true, "session") },
                            modifier = Modifier.refuseObscuredTouches().testTag("approval-grant-session"))
                        DropdownMenuItem({ GrantItem(stringResource(R.string.more_always_allow) + grant.also(a.alsoCovers?.always), grant.ALWAYS_HELP) },
                            { more = false; onDecide(a, true, "always") },
                            modifier = Modifier.refuseObscuredTouches().testTag("approval-grant-always"))
                    }
                }
                Button(onClick = { onDecide(a, true, "once") }) { Text(stringResource(R.string.more_allow_once)) }
            }
        }
    }
}
