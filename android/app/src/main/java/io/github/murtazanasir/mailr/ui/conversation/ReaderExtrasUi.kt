package io.github.murtazanasir.mailr.ui.conversation

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.EditCalendar
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.automirrored.outlined.Label
import androidx.compose.material.icons.outlined.LocationOn
import androidx.compose.material.icons.outlined.MoveToInbox
import androidx.compose.material.icons.outlined.People
import androidx.compose.material.icons.outlined.Report
import androidx.compose.material.icons.automirrored.outlined.StickyNote2
import androidx.compose.material.icons.outlined.Warning
import io.github.murtazanasir.mailr.look.AssistChip
import io.github.murtazanasir.mailr.look.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import io.github.murtazanasir.mailr.look.FilterChip
import androidx.compose.material3.Icon
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.EventChange
import io.github.murtazanasir.mailr.data.api.Invite
import io.github.murtazanasir.mailr.data.api.notSent
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.Message
import io.github.murtazanasir.mailr.data.api.NoteRef
import io.github.murtazanasir.mailr.data.api.OutboxRow
import io.github.murtazanasir.mailr.ui.mail.MailPlace
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import io.github.murtazanasir.mailr.R

/** What the reader shows beyond the thread: invitations, short replies, send states. */
data class ReaderExtrasUi(
    val invites: Map<Long, Invite> = emptyMap(),
    val busy: Set<Long> = emptySet(),
    /** (message id, replies) for the newest message from someone else. */
    val replies: Pair<Long, List<String>>? = null,
    val outbox: List<OutboxRow> = emptyList(),
    /** Each message's translation (Translate.kt), by message id. */
    val translations: Map<Long, Translation> = emptyMap(),
    /** The language Translate… offers first: the last one picked on this phone. */
    val translateTo: String = "English",
    /** Opened attached emails (AttachedEmails.kt), by attachedKey(message, part). */
    val attached: Map<String, AttachedState> = emptyMap(),
    /** Messages whose suggested move was taken or put aside (the card goes), and those on their way. */
    val changesDone: Set<Long> = emptySet(),
    val changesBusy: Set<Long> = emptySet(),
)

/** The invitation card's answers. */
data class InviteCallbacks(
    val answer: (Message, String, Boolean) -> Unit = { _, _, _ -> },
    val applyAnyway: (Message) -> Unit = {},
    val proposal: (Message, Invite, Boolean) -> Unit = { _, _, _ -> },
    val removeCancelled: (Message, Invite) -> Unit = { _, _ -> },
    /** Link to this invitation: my copy under another UID (Invite.calendarMatch) takes its identity. */
    val linkInvite: (Message, String) -> Unit = { _, _ -> },
    /** ACT event.change_suggestion: Move and Not now on a move an email announces in words. */
    val moveSuggested: (Message, EventChange) -> Unit = { _, _ -> },
    val dismissSuggested: (Message) -> Unit = {},
)

/**
 * The invitation card above an iMIP message (ACT invite.view @android.reader): when and where, the
 * organiser, what the ingest job did (ACT invite.apply_reply @android.reader, ACT invite.apply_update @android.reader),
 * the old time of an update, the conflict line, a guest's proposal, Remove after a cancellation, and
 * Yes · Maybe · No (ACT event.rsvp) with "Add to calendar" when it is not there yet.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun InviteCard(m: Message, i: Invite, busy: Boolean, cb: InviteCallbacks) {
    val cs = MaterialTheme.colorScheme
    Surface(color = cs.surfaceContainerHigh, shape = RoundedCornerShape(14.dp),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp).testTag("invite-card")) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Icon(Icons.Outlined.CalendarMonth, null, Modifier.size(20.dp).padding(top = 2.dp), tint = cs.primary)
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(if (i.cancelled) stringResource(R.string.conv_invite_cancelled, i.summary) else i.summary.ifBlank { m.subject },
                        style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
                    Text(InviteText.whenText(i.start, i.end) + if (i.recurring) stringResource(R.string.conv_invite_repeats_suffix) else "",
                        style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                    if (i.location.isNotBlank()) Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.LocationOn, null, Modifier.size(14.dp), tint = cs.onSurfaceVariant)
                        Text(stringResource(R.string.conv_invite_icon_text, i.location), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant,
                            maxLines = 2, overflow = TextOverflow.Ellipsis)
                    }
                    if (i.organizer.email.isNotBlank()) Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.People, null, Modifier.size(14.dp), tint = cs.onSurfaceVariant)
                        val n = i.attendees.size
                        // The address always, beside the name the invitation gives.
                        val organiser = InviteText.organizerLine(i.organizer.name, i.organizer.email)
                        Text(if (n > 0) pluralStringResource(R.plurals.conv_invite_organiser_guests, n, organiser, n)
                            else stringResource(R.string.conv_invite_icon_text, organiser),
                            style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                    }
                }
            }
            InviteText.outcome(i)?.let { text ->
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.testTag("invite-outcome")) {
                    Icon(if (i.itip?.applied == true) Icons.Outlined.CheckCircle else Icons.Outlined.Info, null, Modifier.size(16.dp),
                        tint = if (i.itip?.applied == true) cs.primary else cs.onSurfaceVariant)
                    Text(stringResource(R.string.conv_invite_icon_text, text), style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                    if (i.itip?.pending == true) TextButton(onClick = { cb.applyAnyway(m) }, enabled = !busy) { Text(stringResource(R.string.conv_apply_anyway)) }
                }
            }
            InviteText.previousLine(i)?.let { Text(it, style = MaterialTheme.typography.bodySmall, modifier = Modifier.testTag("invite-diff")) }
            if (!i.cancelled) InviteText.conflictText(i.conflicts.map { it.summary })?.let { text ->
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.testTag("invite-conflict")) {
                    Icon(Icons.Outlined.Warning, null, Modifier.size(15.dp), tint = cs.error)
                    Text(stringResource(R.string.conv_invite_icon_text, text), style = MaterialTheme.typography.bodySmall, color = cs.error)
                }
            }
            InviteText.proposalText(i, m.from.email)?.let { text ->
                FlowRow(verticalArrangement = Arrangement.Center, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.align(Alignment.CenterVertically))
                    TextButton(onClick = { cb.proposal(m, i, true) }, enabled = !busy) { Text(stringResource(R.string.conv_accept)) }
                    TextButton(onClick = { cb.proposal(m, i, false) }, enabled = !busy) { Text(stringResource(R.string.conv_decline)) }
                }
            }
            if (i.cancelled && i.inCalendar) Row(verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.conv_invite_organiser_cancelled), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant,
                    modifier = Modifier.weight(1f))
                TextButton(onClick = { cb.removeCancelled(m, i) }, enabled = !busy) { Text(stringResource(R.string.conv_remove_from_calendar)) }
            }
            if (InviteText.answerable(i)) {
                val current = i.myStatus?.uppercase()
                Text(InviteText.statusLabel(current), style = MaterialTheme.typography.labelMedium, color = cs.onSurfaceVariant)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.testTag("rsvp-row")) {
                    listOf("accepted" to R.string.conv_rsvp_yes, "tentative" to R.string.conv_rsvp_maybe, "declined" to R.string.conv_rsvp_no).forEach { (r, label) ->
                        FilterChip(selected = current == r.uppercase(), onClick = { cb.answer(m, r, true) }, enabled = !busy,
                            label = { Text(stringResource(label)) }, modifier = Modifier.testTag("rsvp-$r"))
                    }
                    if (!i.inCalendar && i.start != null) AssistChip(onClick = { cb.answer(m, "tentative", false) }, enabled = !busy,
                        label = { Text(stringResource(R.string.conv_add_to_calendar)) })
                }
                if (i.inCalendar) Text(stringResource(R.string.conv_in_your_calendar), style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant)
                i.calendarMatch?.takeIf { it.by != "uid" }?.let { match ->
                    TextButton(onClick = { cb.linkInvite(m, match.uid) }, enabled = !busy, modifier = Modifier.testTag("invite-link")) {
                        Text(stringResource(R.string.conv_invite_link))
                    }
                }
            }
        }
    }
}

/**
 * "Move “Ops Review” to Wed 28 Oct, 1:00 PM – 3:00 PM?" (ACT event.change_suggestion @android.reader): one
 * suggestion for a move an email announces in words, as the web's EventChangeCard. The event's time now,
 * the sentence it came from, then Move or Not now. Nothing changes until Move; Move has Undo.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun EventChangeCard(m: Message, c: EventChange, busy: Boolean, cb: InviteCallbacks) {
    val cs = MaterialTheme.colorScheme
    Surface(color = cs.surfaceContainerHigh, shape = RoundedCornerShape(14.dp),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp).testTag("event-change")) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                // TalkBack starts the card with what it is (the web's aria-label).
                Icon(Icons.Outlined.EditCalendar, stringResource(R.string.conv_change_suggested), Modifier.size(20.dp).padding(top = 2.dp), tint = cs.primary)
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(stringResource(R.string.conv_change_move_to, c.summary, InviteText.whenText(c.newStart, c.newEnd)),
                        style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold, modifier = Modifier.semantics { heading() })
                    Text(stringResource(R.string.conv_change_now_at, InviteText.whenText(c.oldStart, c.oldEnd)),
                        style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                    if (c.sentence.isNotBlank()) Row(Modifier.padding(top = 6.dp).height(IntrinsicSize.Min).testTag("event-change-quote")) {
                        // The quote's line sits at the start edge, so it follows right-to-left text.
                        Box(Modifier.width(2.dp).fillMaxHeight().background(cs.outlineVariant).testTag("event-change-quote-line"))
                        Spacer(Modifier.width(10.dp))
                        Text(c.sentence, style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                    }
                }
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { cb.moveSuggested(m, c) }, enabled = !busy, modifier = Modifier.testTag("event-change-move")) {
                    Text(stringResource(R.string.conv_change_move))
                }
                TextButton(onClick = { cb.dismissSuggested(m) }, enabled = !busy, modifier = Modifier.testTag("event-change-dismiss")) {
                    Text(stringResource(R.string.conv_change_not_now))
                }
            }
        }
    }
}

/** ACT mail.reply_suggestions @android.reader: three short replies under the newest message; a tap opens the reply with it. */
@Composable
fun ReplyChips(replies: List<String>, onPick: (String) -> Unit) {
    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp).testTag("reply-chips"),
        horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        Icon(Icons.Outlined.AutoAwesome, stringResource(R.string.conv_suggested_replies), Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        replies.forEach { r -> AssistChip(onClick = { onPick(r) }, label = { Text(r, maxLines = 1, overflow = TextOverflow.Ellipsis) }) }
    }
}

/** A draft that did not leave: "Not sent" with the reason and Retry (ACT draft.retry_send); on its way: "Sending…". */
@Composable
fun SendStateLine(row: OutboxRow, onRetry: (OutboxRow) -> Unit) {
    val cs = MaterialTheme.colorScheme
    val failed = row.notSent()
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 2.dp).testTag("send-state"), verticalAlignment = Alignment.CenterVertically) {
        Icon(if (failed) Icons.Outlined.ErrorOutline else Icons.Outlined.Info, null, Modifier.size(16.dp), tint = if (failed) cs.error else cs.onSurfaceVariant)
        Text(when (row.status) {
            "failed" -> stringResource(R.string.conv_not_sent, plainSendError(row.error))
            "uncertain" -> stringResource(R.string.mail_outbox_maybe_sent, plainSendError(row.error))
            "awaiting_approval" -> stringResource(R.string.conv_waiting_approval)
            else -> stringResource(R.string.conv_sending)
        }, style = MaterialTheme.typography.bodySmall, color = if (failed) cs.error else cs.onSurfaceVariant, modifier = Modifier.weight(1f))
        if (failed) TextButton(onClick = { onRetry(row) }) { Text(stringResource(R.string.conv_retry)) }
    }
}

/** ACT mail.move @android.reader: Move to: Archive, Spam or Trash (Inbox from Spam or Trash), then a label, with a filter. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MoveToSheet(labels: List<Label>, place: MailPlace, onDismiss: () -> Unit, onPick: (MoveTarget) -> Unit) {
    var filter by remember { mutableStateOf("") }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.navigationBarsPadding().testTag("move-sheet")) {
            Text(stringResource(R.string.conv_move_to_title), style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(horizontal = 24.dp).semantics { heading() })
            Text(stringResource(R.string.conv_move_removes_inbox), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 24.dp, vertical = 2.dp))
            OutlinedTextField(filter, { filter = it }, singleLine = true, label = { Text(stringResource(R.string.conv_search_labels)) },
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp))
            val targets = MoveTo.targets(labels, place, filter)
            LazyColumn(Modifier.heightIn(max = 420.dp)) {
                items(targets, key = { "${it.action}-${it.labelId}" }) { t ->
                    ListItem(headlineContent = { Text(t.label) },
                        leadingContent = { Icon(when (t.action) {
                            "archive" -> Icons.Outlined.Archive; "spam" -> Icons.Outlined.Report; "trash" -> Icons.Outlined.Delete
                            "untrash", "not_spam" -> Icons.Outlined.MoveToInbox; else -> Icons.AutoMirrored.Outlined.Label
                        }, null) },
                        modifier = Modifier.clickable { onPick(t) })
                }
                if (targets.isEmpty()) item { Text(stringResource(R.string.conv_no_labels_match), Modifier.padding(24.dp), color = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
        }
    }
}

/** ACT note.link @android.reader: "Link to a note…": search the notes, a tap links this conversation to one. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LinkNoteSheet(find: suspend (String) -> List<NoteRef>, onDismiss: () -> Unit, onPick: (NoteRef) -> Unit) {
    var q by remember { mutableStateOf("") }
    var found by remember { mutableStateOf<List<NoteRef>?>(null) }
    LaunchedEffect(q) {
        kotlinx.coroutines.delay(if (q.isEmpty()) 0 else 250)
        found = try { find(q) } catch (e: kotlinx.coroutines.CancellationException) { throw e } catch (_: Exception) { emptyList() }
    }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.navigationBarsPadding().testTag("link-note-sheet")) {
            Text(stringResource(R.string.conv_link_note_title), style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(horizontal = 24.dp).semantics { heading() })
            OutlinedTextField(q, { q = it }, singleLine = true, label = { Text(stringResource(R.string.conv_search_notes)) },
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp))
            LazyColumn(Modifier.heightIn(min = 120.dp, max = 420.dp)) {
                val rows = found
                if (rows == null) item { Text(stringResource(R.string.conv_loading), Modifier.padding(24.dp), color = MaterialTheme.colorScheme.onSurfaceVariant) }
                else if (rows.isEmpty()) item { Text(if (q.isBlank()) stringResource(R.string.conv_no_notes_yet) else stringResource(R.string.conv_no_notes_match), Modifier.padding(24.dp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant) }
                else items(rows, key = { it.id }) { n ->
                    ListItem(headlineContent = { Text(n.title.ifBlank { stringResource(R.string.conv_untitled) }, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                        leadingContent = { Icon(Icons.AutoMirrored.Outlined.StickyNote2, null) },
                        supportingContent = { Text(if (n.kind == "checklist") stringResource(R.string.conv_kind_checklist) else stringResource(R.string.conv_kind_note)) },
                        modifier = Modifier.clickable { onPick(n) })
                }
            }
        }
    }
}
