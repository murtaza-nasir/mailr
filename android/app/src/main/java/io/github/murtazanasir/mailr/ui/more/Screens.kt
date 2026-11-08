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
import androidx.compose.material.icons.outlined.ArrowDropDown
import androidx.compose.material.icons.outlined.MoreTime
import androidx.compose.material.icons.outlined.Info
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.ui.present.factFields
import io.github.murtazanasir.mailr.ui.present.longFields
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

// ======================= approvals =======================

// The Approvals screen is in ui/approvals/ApprovalsUi.kt. ApprovalCard is one pending approval, on
// that screen and in the assistant's chat.

/** A grant in the card's Allow… menu: its label, and under it what it covers. */
@Composable
private fun GrantItem(label: String, help: String) {
    Column(Modifier.widthIn(max = 320.dp).padding(vertical = 4.dp)) {
        Text(label, style = MaterialTheme.typography.bodyLarge)
        Text(help, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** "claude-code · 1 hr ago": who asked and when, for the card's head line. */
fun requestedLine(a: Approval): String =
    listOfNotNull(a.actor.ifBlank { AppText.get(R.string.more_an_agent) }, Format.parse(a.createdAt)?.let { Format.relative(it) })
        .joinToString(" · ")

/** How many other waiting requests the card's grants also approve: "Allow for 12 hours" first, else "Always allow". */
fun grantCount(a: Approval): Int = a.alsoCovers?.let { if (it.session > 0) it.session else it.always } ?: 0

/**
 * One approval: the server's display model (ui/present/ApprovalBody.kt; older servers send title and
 * lines), the decision buttons while it waits, and once decided its outcome in place ("Approved ·
 * moved 6 messages to Trash") with the details folded away. ACT approval.approve @android.approvals @android.assistant, ACT approval.deny @android.approvals @android.assistant
 *
 * The card reads top to bottom: a head line with the kind of request ("New task") and who asked when;
 * the object's own title as the heading (two lines, with More for the rest); the short facts on one or
 * two lines and any longer value (a quote, a list) below; one line of why it asks, with ⓘ for the whole
 * reason; and one row of answers: Allow once, Deny, the grants ("+1 ▾") and ⋮.
 *
 * [onAsk] adds Ask to ⋮: the ask capture with this approval as its chip, so the assistant can explain it.
 * Only the buttons here decide it. ACT agent.ask_about @android.assistant @android.approvals
 *
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
    var grantsOpen by remember(a.id) { mutableStateOf(false) }
    var moreOpen by remember(a.id) { mutableStateOf(false) }
    var details by remember(a.id) { mutableStateOf(false) }
    var titleAll by remember(a.id) { mutableStateOf(false) }
    var titleLong by remember(a.id) { mutableStateOf(false) }
    var why by remember(a.id) { mutableStateOf(false) }
    val d = a.shown
    val review = a.review
    // A review card: the user's own moves on it, by conversation id, sent with Apply (apply_changes).
    var changes by androidx.compose.runtime.saveable.rememberSaveable(a.id) { mutableStateOf(emptyMap<Long, String>()) }
    val pending = a.status == "pending"
    val outcome = a.outcome?.takeIf { it.state == a.status }?.localized() ?: io.github.murtazanasir.mailr.ui.present.localOutcome(a.status)
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    // A pending card (on the Approvals screen or in the assistant chat) hides other apps' overlays,
    // so none can cover its text while the buttons stay clear.
    if (pending) io.github.murtazanasir.mailr.ui.components.SensitiveScreen()
    val heading = a.heading
    val neededDesc = stringResource(R.string.more_approval_needed_desc, heading)
    val outcomeDesc = stringResource(R.string.more_approval_outcome_desc, heading, outcome.text)
    Card(Modifier.widthIn(max = 640.dp).fillMaxWidth().semantics { contentDescription = if (pending) neededDesc else outcomeDesc },
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
        border = if (pending) androidx.compose.foundation.BorderStroke(1.dp,
            if (d?.danger == true) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary) else null) {
        Column(Modifier.padding(16.dp)) {
            // The head line: the kind of request, and who asked when. The label is measured first, so a long
            // requester name is cut, never the label.
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().testTag("approval-head")) {
                Icon(io.github.murtazanasir.mailr.ui.present.displayIcon(d?.icon), null, Modifier.size(16.dp),
                    tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(6.dp))
                a.typeLabel?.let {
                    Text(it, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary, maxLines = 1,
                        modifier = Modifier.testTag("approval-kind"))
                    Spacer(Modifier.width(12.dp))
                }
                Text(requestedLine(a), style = MaterialTheme.typography.bodySmall, color = muted, maxLines = 1,
                    overflow = TextOverflow.Ellipsis, textAlign = TextAlign.End,
                    modifier = Modifier.weight(1f).testTag("approval-requested"))
            }
            Spacer(Modifier.height(6.dp))
            // The object's own title, two lines at most; More shows the rest.
            Text(heading, style = MaterialTheme.typography.titleMedium, maxLines = if (titleAll) Int.MAX_VALUE else 2,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.testTag("approval-heading"),
                onTextLayout = { if (!titleAll) titleLong = it.hasVisualOverflow })
            if (titleLong || titleAll) androidx.compose.material3.TextButton(onClick = { titleAll = !titleAll },
                contentPadding = PaddingValues(horizontal = 0.dp), modifier = Modifier.height(32.dp).testTag("approval-title-more")) {
                Text(stringResource(if (titleAll) R.string.present_show_less else R.string.present_show_more),
                    style = MaterialTheme.typography.labelMedium)
            }
            if (!pending) {
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(outcome.text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f),
                        color = when (a.status) {
                            "executed" -> MaterialTheme.colorScheme.primary
                            "denied", "failed", "expired" -> MaterialTheme.colorScheme.error
                            else -> muted
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
                    // The short fields on one line, the longer ones (the email's text, a list) in their box.
                    val facts = d.factFields(a.head?.name)
                    if (facts.isNotEmpty()) Text(io.github.murtazanasir.mailr.ui.present.factsText(facts, muted),
                        style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 4.dp).testTag("approval-facts"))
                    val long = d.longFields()
                    if (d.danger && pending) Text(stringResource(R.string.more_cant_undo), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(top = 6.dp))
                    if (long.isNotEmpty() || d.summary != null) {
                        Spacer(Modifier.height(8.dp))
                        io.github.murtazanasir.mailr.ui.present.ApprovalBody(d, fields = long, showReason = false)
                    }
                } else if (a.preview.lines.isNotEmpty() || a.target.isNotBlank()) {
                    Spacer(Modifier.height(8.dp))
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
            // Why it asks: one short line, and ⓘ for the whole reason.
            a.shortReason?.let { short ->
                val whyDesc = stringResource(if (why) R.string.more_why_hide else R.string.more_why_asks)
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 4.dp).testTag("approval-reason")) {
                    Text(short, style = MaterialTheme.typography.bodySmall, color = muted, modifier = Modifier.weight(1f, fill = false))
                    IconButton(onClick = { why = !why }, modifier = Modifier.size(32.dp).testTag("approval-why")
                        .semantics { contentDescription = whyDesc }) {
                        Icon(Icons.Outlined.Info, null, Modifier.size(18.dp), tint = muted)
                    }
                }
                if (why) Text(d?.reason ?: short, style = MaterialTheme.typography.bodySmall, color = muted,
                    modifier = Modifier.padding(bottom = 4.dp).testTag("approval-why-text"))
            }
            a.expiresAt?.let { Format.parse(it) }?.let {
                Text(stringResource(R.string.more_expires_in, Format.relative(AppClock.now(), it, ago = false)),
                    style = MaterialTheme.typography.labelSmall, color = muted,
                    modifier = Modifier.padding(top = 4.dp))
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
                        color = muted, modifier = Modifier.testTag("review-changed"))
                    Button(onClick = { onApplyReview?.invoke(a, changes) ?: onDecide(a, true, "once") }, modifier = Modifier.testTag("review-apply")) {
                        Text(stringResource(R.string.asst_review_apply))
                    }
                }
                if (onAsk != null) CardMenu(a, onAsk, moreOpen) { moreOpen = it }
                return@Column
            }
            // One row of answers. The buttons drop touches that come through another app's overlay (tapjacking).
            Row(Modifier.fillMaxWidth().refuseObscuredTouches().testTag("approval-actions"), horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Button(onClick = { onDecide(a, true, "once") }, contentPadding = PaddingValues(horizontal = 16.dp),
                    modifier = Modifier.testTag("approval-allow")) { Text(stringResource(R.string.more_allow_once), maxLines = 1) }
                OutlinedButton(onClick = { onDecide(a, false, "once") }, contentPadding = PaddingValues(horizontal = 16.dp),
                    modifier = Modifier.testTag("approval-deny")) { Text(stringResource(R.string.more_deny), maxLines = 1) }
                Spacer(Modifier.weight(1f))
                // A card a hard rule held (grants false) is decided once: no grants menu, as on the web.
                if (a.outboxId == null && a.grants) Box {
                    val n = grantCount(a)
                    val grantsDesc = if (n > 0) pluralStringResource(R.plurals.more_allow_menu_also, n, n) else stringResource(R.string.more_allow_menu)
                    io.github.murtazanasir.mailr.look.TextButton(onClick = { grantsOpen = true }, contentPadding = PaddingValues(horizontal = 8.dp),
                        modifier = Modifier.testTag("approval-grants").semantics { contentDescription = grantsDesc }) {
                        // The count of others it approves too, else a clock: the grants are allowing for longer.
                        if (n > 0) Text("+$n", maxLines = 1) else Icon(Icons.Outlined.MoreTime, null, Modifier.size(18.dp))
                        Icon(Icons.Outlined.ArrowDropDown, null, Modifier.size(20.dp))
                    }
                    // Each grant says what it covers, and how many other waiting requests it approves too
                    // (io.github.murtazanasir.mailr.ui.approvals.GrantText).
                    val grant = io.github.murtazanasir.mailr.ui.approvals.GrantText
                    DropdownMenu(grantsOpen, { grantsOpen = false }) {
                        DropdownMenuItem({ GrantItem(grant.sessionLabel(a) + grant.also(a.alsoCovers?.session), grant.sessionHelp(a)) },
                            { grantsOpen = false; onDecide(a, true, "session") },
                            modifier = Modifier.refuseObscuredTouches().testTag("approval-grant-session"))
                        DropdownMenuItem({ GrantItem(stringResource(R.string.more_always_allow) + grant.also(a.alsoCovers?.always), grant.ALWAYS_HELP) },
                            { grantsOpen = false; onDecide(a, true, "always") },
                            modifier = Modifier.refuseObscuredTouches().testTag("approval-grant-always"))
                    }
                }
                // Not on a legacy outbox row: its id is not a pending action's.
                if (onAsk != null && a.outboxId == null) CardMenu(a, onAsk, moreOpen) { moreOpen = it }
            }
        }
    }
}

/** The card's ⋮: Ask (the assistant explains the request; only the card's buttons decide it). */
@Composable
private fun CardMenu(a: Approval, onAsk: (Approval) -> Unit, open: Boolean, setOpen: (Boolean) -> Unit) {
    val name = io.github.murtazanasir.mailr.ui.assistantName()
    val askDesc = stringResource(R.string.more_ask_about_approval, name)
    Box {
        IconButton(onClick = { setOpen(true) }, modifier = Modifier.size(40.dp).testTag("approval-more")) {
            Icon(Icons.Outlined.MoreVert, stringResource(R.string.more_card_menu))
        }
        DropdownMenu(open, { setOpen(false) }) {
            DropdownMenuItem({ Text(stringResource(R.string.more_ask_ambrose, name)) }, { setOpen(false); onAsk(a) },
                leadingIcon = { Icon(Icons.Outlined.AutoAwesome, null) },
                modifier = Modifier.testTag("approval-ask").semantics { contentDescription = askDesc })
        }
    }
}
