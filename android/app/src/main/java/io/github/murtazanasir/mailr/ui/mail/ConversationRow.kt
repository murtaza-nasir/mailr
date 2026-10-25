package io.github.murtazanasir.mailr.ui.mail

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.AttachFile
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.DeleteForever
import androidx.compose.material.icons.outlined.MoveToInbox
import androidx.compose.material.icons.outlined.RestoreFromTrash
import androidx.compose.material.icons.outlined.Drafts
import androidx.compose.material.icons.outlined.MarkEmailUnread
import androidx.compose.material.icons.outlined.NotificationsActive
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.StarOutline
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.platform.testTag
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.layout.layout
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.foundation.text.InlineTextContent
import androidx.compose.foundation.text.appendInlineContent
import androidx.compose.ui.text.Placeholder
import androidx.compose.ui.text.PlaceholderVerticalAlign
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.PrimaryAction
import io.github.murtazanasir.mailr.data.settings.SwipeAction
import io.github.murtazanasir.mailr.ui.components.Avatar
import io.github.murtazanasir.mailr.ui.components.ImportanceMarker
import io.github.murtazanasir.mailr.ui.components.darkSurface
import io.github.murtazanasir.mailr.ui.theme.Mail
import io.github.murtazanasir.mailr.ui.util.Format
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats

@Composable
fun ConversationRow(
    c: ConversationSummary,
    labels: Map<Long, Label>,
    selected: Boolean,
    selectionMode: Boolean,
    activated: Boolean,
    compact: Boolean,
    swipeStart: SwipeAction,
    swipeEnd: SwipeAction,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
    onToggleSelect: () -> Unit,
    onStar: () -> Unit,
    onSwipe: (SwipeAction) -> Unit,
    modifier: Modifier = Modifier,
    /** Labels the list itself is about ([viewLabelIds]); their chips would only repeat the view. */
    hiddenLabelIds: Set<Long> = emptySet(),
    /** In Spam and Trash the swipes restore, rescue or delete forever. */
    place: MailPlace = MailPlace.OTHER,
    /** Archive, or Move to Inbox for a row not in the Inbox ([archiveSlot]). */
    archiveSlot: ArchiveSlot = ArchiveSlot.ARCHIVE,
) {
    val haptics = LocalHapticFeedback.current
    // Swipes must be deliberate (ui/components/DeliberateSwipe.kt). ACT mail.archive @android.list, ACT mail.delete @android.list
    io.github.murtazanasir.mailr.ui.components.SwipeRow(
        enableStart = !selectionMode && swipeStart != SwipeAction.NONE,
        enableEnd = !selectionMode && swipeEnd != SwipeAction.NONE,
        onSwipe = { v ->
            val action = when (v) {
                SwipeToDismissBoxValue.StartToEnd -> swipeStart
                SwipeToDismissBoxValue.EndToStart -> swipeEnd
                SwipeToDismissBoxValue.Settled -> null
            }
            if (action != null && action != SwipeAction.NONE) {
                haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                onSwipe(action)
                // Removing actions let the list drop the row; others snap back. Delete forever
                // asks first, so its row stays until the answer.
                // Move to Inbox keeps the row where it is.
                action in setOf(SwipeAction.ARCHIVE, SwipeAction.TRASH, SwipeAction.SNOOZE) &&
                    !(place == MailPlace.TRASH && action == SwipeAction.TRASH) &&
                    !(archiveSlot == ArchiveSlot.INBOX && action == SwipeAction.ARCHIVE)
            } else false
        },
        background = { dir, _ -> SwipeBackground(dir, swipeStart, swipeEnd, c.unread, place, archiveSlot) },
        modifier = modifier,
        resetKey = Triple(c.id, c.unreadCount, c.starred),
    ) {
        RowContent(c, labels, hiddenLabelIds, selected, selectionMode, activated, compact, onClick, onLongClick, onToggleSelect, onStar,
            onSwipe, place, archiveSlot)
    }
}

@Composable
private fun RowContent(
    c: ConversationSummary,
    labels: Map<Long, Label>,
    hiddenLabelIds: Set<Long>,
    selected: Boolean,
    selectionMode: Boolean,
    activated: Boolean,
    compact: Boolean,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
    onToggleSelect: () -> Unit,
    onStar: () -> Unit,
    onSwipe: (SwipeAction) -> Unit,
    place: MailPlace = MailPlace.OTHER,
    archiveSlot: ArchiveSlot = ArchiveSlot.ARCHIVE,
) {
    val cs = MaterialTheme.colorScheme
    // The web's look (list.css .conv-row): read rows on a tinted band, a 1 px divider under each row,
    // a 32 px avatar, one line of snippet.
    val web = io.github.murtazanasir.mailr.look.LocalLook.current
    val bg by animateColorAsState(
        when {
            selected -> if (web.web) web.palette.rowSelected else cs.secondaryContainer
            activated -> cs.surfaceContainerHigh
            web.web -> if (c.unread) web.palette.rowUnreadBg else web.palette.rowReadBg
            else -> cs.surface
        }, label = "rowBg", // i18n-ignore: animation label
    )
    val first = c.participants.firstOrNull { !it.me } ?: c.participants.firstOrNull()
    val unread = c.unread
    val pad = if (web.web) 10.dp else if (compact) 8.dp else 12.dp
    val shownLabels = rowLabels(c, labels, hiddenLabelIds)
    val snippet = rowSnippet(c.subject, c.snippet)
    // Attachments ride on the snippet line; with no snippet they become a paperclip by the date.
    val hasAttachments = c.attachmentsPreview.isNotEmpty() || c.hasAttachments
    Surface(color = bg, modifier = if (web.web) io.github.murtazanasir.mailr.look.Hairline.bottom(web.palette.divider) else Modifier) {
        Row(
            Modifier
                .fillMaxWidth()
                .combinedClickable(
                    onClick = { if (selectionMode) onToggleSelect() else onClick() },
                    onLongClick = onLongClick,
                    onClickLabel = stringResource(R.string.mail_row_open),
                    onLongClickLabel = stringResource(R.string.mail_row_select),
                )
                .semantics {
                    stateDescription = buildString {
                        if (unread) append(AppText.get(R.string.mail_row_state_unread))
                        if (c.starred) append(AppText.get(R.string.mail_row_state_starred))
                        if (c.important) append(AppText.get(R.string.mail_row_state_important))
                    }
                    customActions = listOf(
                        CustomAccessibilityAction(swipeLabel(place, SwipeAction.ARCHIVE, archiveSlot) ?: AppText.get(R.string.mail_archive)) { onSwipe(SwipeAction.ARCHIVE); true },
                        CustomAccessibilityAction(swipeLabel(place, SwipeAction.TRASH) ?: AppText.get(R.string.mail_delete)) { onSwipe(SwipeAction.TRASH); true },
                        CustomAccessibilityAction(AppText.get(if (unread) R.string.mail_mark_read else R.string.mail_mark_unread)) { onSwipe(SwipeAction.READ); true },
                        CustomAccessibilityAction(AppText.get(R.string.mail_snooze)) { onSwipe(SwipeAction.SNOOZE); true },
                    )
                }
                .padding(start = if (web.web) 12.dp else 16.dp, end = 4.dp, top = pad, bottom = pad),
            verticalAlignment = Alignment.Top,
        ) {
            Box(Modifier.padding(top = 2.dp).combinedClickable(onClick = onToggleSelect, onClickLabel = stringResource(R.string.mail_row_select))) {
                Avatar(first?.name, first?.email ?: "?", size = if (web.web) 32.dp else if (compact) 36.dp else 40.dp, selected = selected)
            }
            Spacer(Modifier.width(if (web.web) 12.dp else 14.dp))
            Column(Modifier.weight(1f)) {
                // Top line: sender, then the labels, the paperclip, the snooze clock and the date.
                // The meta is measured first, so the sender is the part that gets cut short.
                BoxWithConstraints(Modifier.fillMaxWidth()) {
                    val maxChips = if (maxWidth >= 300.dp) 2 else 1
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically) {
                            ImportanceMarker(c.important, Modifier.padding(end = 4.dp))
                            Text(
                                rowSender(c),
                                style = MaterialTheme.typography.titleSmall.copy(fontWeight = if (unread) FontWeight.Bold else FontWeight.Normal),
                                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false),
                            )
                            if (c.messageCount > 1) {
                                Text(stringResource(R.string.mail_row_count, c.messageCount), style = MaterialTheme.typography.labelMedium, color = cs.onSurfaceVariant,
                                    maxLines = 1)
                            }
                            if (c.draftCount > 0) {
                                Text(stringResource(R.string.mail_row_draft), style = MaterialTheme.typography.labelMedium, color = cs.error, maxLines = 1)
                            }
                        }
                        RowLabelChips(shownLabels, maxChips, Modifier.padding(start = 6.dp))
                        if (snippet.isEmpty() && hasAttachments) {
                            AttachmentInline(c, Modifier.padding(start = 6.dp))
                        }
                        if (c.snoozedUntil != null) {
                            Icon(Icons.Outlined.Schedule, stringResource(R.string.mail_snoozed), Modifier.padding(start = 6.dp).size(14.dp), tint = Mail.colors.swipeSnooze)
                        }
                        RowAccounts(c, Modifier.padding(start = 6.dp))
                        Text(
                            Format.listDate(c.lastDate),
                            style = MaterialTheme.typography.labelMedium.let { if (unread) it.copy(fontWeight = FontWeight.Bold, color = cs.onSurface) else it.copy(color = cs.onSurfaceVariant) },
                            maxLines = 1,
                            modifier = Modifier.padding(start = 8.dp, end = 12.dp),
                        )
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        // Subject line: the subject, then the triage signals and the No reply mark.
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                c.subject.ifBlank { stringResource(R.string.mail_no_subject) },
                                style = MaterialTheme.typography.bodyMedium.let { if (unread) it.copy(fontWeight = if (web.web) FontWeight.Bold else FontWeight.SemiBold) else it },
                                color = if (unread) cs.onSurface else cs.onSurface.copy(alpha = 0.92f),
                                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false),
                            )
                            SignalChips(shownSignals(c, labels)) // none in Spam or Trash (SignalGuard.kt)
                            // A search that matched only inside an attached email.
                            if (c.matchInAttached) {
                                Spacer(Modifier.width(6.dp))
                                Surface(shape = RoundedCornerShape(50), color = cs.surfaceContainerHighest,
                                    modifier = Modifier.testTag("match-in-attached")) {
                                    Text(stringResource(R.string.mail_match_in_attached), style = MaterialTheme.typography.labelSmall,
                                        color = cs.onSurfaceVariant, maxLines = 1, modifier = Modifier.padding(horizontal = 7.dp, vertical = 2.dp))
                                }
                            }
                            // ACT mail.follow_up @android.list: a follow-up reminder brought it back with no reply.
                            if (c.nudgedAt != null) {
                                Spacer(Modifier.width(6.dp))
                                NoReplyPill()
                            }
                        }
                        // A missing snippet leaves no empty line behind.
                        if (snippet.isNotEmpty()) {
                            // The attachments lead the snippet inline, so the text keeps the full width.
                            val files = if (hasAttachments) attachmentText(c) else null
                            val attachmentsAlt = stringResource(R.string.mail_row_attachments)
                            Text(
                                buildAnnotatedString {
                                    if (files != null) {
                                        appendInlineContent(CLIP, attachmentsAlt)
                                        if (files.isNotEmpty()) withStyle(SpanStyle(fontWeight = FontWeight.Medium, fontSize = 13.sp)) {
                                            append(files)
                                        }
                                        append("  ")
                                    }
                                    append(snippet)
                                },
                                inlineContent = if (files != null) clipInline(cs.onSurfaceVariant) else emptyMap(),
                                style = MaterialTheme.typography.bodyMedium,
                                color = cs.onSurfaceVariant,
                                maxLines = if (compact || web.web) 1 else 2, overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                    // 40dp drawn, but it claims only a text line of height, so a row without a snippet
                    // stays one line shorter; the touch target still grows to the 48dp minimum.
                    IconButton(onClick = onStar, modifier = Modifier.layout { m, c ->
                        val p = m.measure(c)
                        val h = minOf(p.height, 24.dp.roundToPx())
                        layout(p.width, h) { p.place(0, (h - p.height) / 2) }
                    }.size(40.dp)) {
                        if (c.starred) Icon(Icons.Filled.Star, stringResource(R.string.mail_unstar), tint = Mail.colors.star)
                        else Icon(Icons.Outlined.StarOutline, stringResource(R.string.mail_star), tint = cs.onSurfaceVariant)
                    }
                }
                // ACT mail.primary_action @android.list @android.reader: the email's main action as a chip; the link opens in the browser.
                c.primaryAction?.takeIf { it.openable }?.let { pa ->
                    Spacer(Modifier.height(6.dp))
                    PrimaryActionChip(pa, Modifier.padding(end = 12.dp))
                }
            }
        }
    }
}

/**
 * The row's people. A note to self (the server's `to_self`: the user wrote every message, each only to
 * their own addresses; not a drafts-only thread) reads "Note to self". A conversation only the user
 * wrote in that went to other people (a sent email without a reply) reads "To: Dana Whitfield", as in
 * Gmail's Sent list. Otherwise the senders. The web does the same (rowText.ts rowSender).
 */
internal fun rowSender(c: ConversationSummary): String {
    val own = c.participants.isNotEmpty() && c.participants.all { it.me }
    return when {
        own && c.toSelf && c.messageCount > c.draftCount -> AppText.get(R.string.mail_note_to_self)
        own && !c.toSelf && c.recipients.isNotEmpty() ->
            AppText.get(R.string.mail_to_recipients, Format.participants(c.recipients.map { it.copy(me = false) }, 1))
        else -> Format.participants(c.participants, c.messageCount)
    }
}

/**
 * The snippet as the row shows it. A snippet that repeats the subject (a screenshot sent to
 * oneself, a note whose first line became the subject, a "Subject - body" header, a subject
 * printed twice) loses that repeat, and one that is only the subject becomes empty. The repeat
 * goes only when the whole subject ends at a sentence or line boundary: the end of the text, a
 * newline, or a separator (. : ; — – - | ·) followed by whitespace. "Your code is 715204"
 * under the subject "Your code" is one sentence and stays whole. The web does the same
 * (rowText.ts rowSnippet).
 */
internal fun rowSnippet(subject: String, snippet: String): String {
    // Format.snippet's clean-up without folding newlines, so a line break after the subject counts.
    val raw = snippet.replace(Regex("(^|\\s)#{1,6}[^\\S\\n]+"), "$1").replace(Regex("\\*\\*|__"), "").trim()
    val s = Format.snippet(raw)
    val subj = Format.snippet(subject)
    if (s.isEmpty() || subj.isEmpty() || !s.startsWith(subj, ignoreCase = true)) return s
    // Walk the raw snippet past the subject's characters.
    var i = 0
    for (ch in subj) {
        if (ch.isWhitespace()) { while (i < raw.length && raw[i].isWhitespace()) i++ } else i++
    }
    val rest = raw.substring(i.coerceAtMost(raw.length))
    if (rest.isBlank()) return ""
    // A subject printed twice ("Title Title body", a heading over the same bold title) loses one copy.
    val again = Format.snippet(rest)
    if (rest.first().isWhitespace() && again.startsWith(subj, ignoreCase = true) &&
        again.getOrNull(subj.length)?.isLetterOrDigit() != true) return rowSnippet(subject, rest)
    if (Regex("^[^\\S\\n]*\\n").containsMatchIn(rest)) return Format.snippet(rest)
    val sep = Regex("^\\s*[.:;—–\\-|·]+(?:\\s+|$)").find(rest) ?: return s
    return Format.snippet(rest.substring(sep.value.length))
}

/**
 * User labels on a row, without the label the list itself is about (the "Later" tab, a label's
 * own view). System labels (Inbox, Sent…) are implied by the view and never show.
 */
internal fun rowLabels(c: ConversationSummary, labels: Map<Long, Label>, hidden: Set<Long>): List<Label> =
    c.labelIds.mapNotNull { labels[it] }.filter { it.role == null && it.id !in hidden }

/**
 * The labels a list is about. [contextLabelId] is the label the view or tab is built on, matched
 * by id (as the web's hideLabelId): a tab called "Read later" built on the label "Later" hides
 * "Later" and nothing else. Without one, a label's view or an inbox tab named after a label.
 */
fun viewLabelIds(view: io.github.murtazanasir.mailr.data.repo.MailView, labels: Collection<Label>, contextLabelId: Long? = null): Set<Long> {
    if (contextLabelId != null) return setOf(contextLabelId)
    val own = view.view.takeIf { it.startsWith("label:") && view.q.isNullOrBlank() }?.removePrefix("label:")?.toLongOrNull()
    val named = if (view.tab != null || own != null)
        labels.filter { it.role == null && (it.name.equals(view.title, true) || it.leaf.equals(view.title, true)) }.map { it.id }
    else emptyList()
    return (listOfNotNull(own) + named).toSet()
}

/** Compact label chips on the top line: at most [max], then "+n" for the rest. */
@Composable
private fun RowLabelChips(shown: List<Label>, max: Int, modifier: Modifier = Modifier) {
    if (shown.isEmpty()) return
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically) {
        shown.take(max).forEach { RowLabelChip(it) }
        if (shown.size > max) {
            Text("+${shown.size - max}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1)
        }
    }
}

@Composable
private fun RowLabelChip(label: Label) {
    val (bg, fg) = Format.labelColors(label.color, darkSurface())
        ?: (MaterialTheme.colorScheme.secondaryContainer to MaterialTheme.colorScheme.onSecondaryContainer)
    if (io.github.murtazanasir.mailr.look.LocalLook.current.web) {
        // components.css .label-chip: 18 px tall, 12 px text, 4 px corners.
        Surface(color = bg, contentColor = fg, shape = RoundedCornerShape(io.github.murtazanasir.mailr.look.WebTokens.RadiusXs), modifier = Modifier.widthIn(max = 112.dp)) {
            Text(label.leaf, style = io.github.murtazanasir.mailr.look.WebTheme.style(io.github.murtazanasir.mailr.look.WebTokens.LabelChip),
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(horizontal = 5.dp))
        }
        return
    }
    Surface(color = bg, contentColor = fg, shape = RoundedCornerShape(4.dp), modifier = Modifier.widthIn(max = 84.dp)) {
        Text(label.leaf, style = MaterialTheme.typography.labelSmall.copy(fontSize = 11.sp, lineHeight = 14.sp),
            maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(horizontal = 5.dp, vertical = 1.dp))
    }
}

private const val CLIP = "clip"

private fun clipInline(tint: Color) = mapOf(CLIP to InlineTextContent(
    Placeholder(16.sp, 14.sp, PlaceholderVerticalAlign.TextCenter)) {
    Icon(Icons.Outlined.AttachFile, null, Modifier.fillMaxSize(), tint = tint)
})

/** "report.pdf", "report.pdf +2", or "" when only the flag is known; long names keep both ends. */
internal fun attachmentText(c: ConversationSummary): String {
    val files = c.attachmentsPreview
    if (files.isEmpty()) return ""
    val name = files[0].filename.let { if (it.length > 24) it.take(13) + "…" + it.takeLast(10) else it }
    return name + if (files.size > 1) AppText.get(R.string.mail_attachment_more, files.size - 1) else ""
}

/** A paperclip with the count, on the top line of a row without a snippet. */
@Composable
private fun AttachmentInline(c: ConversationSummary, modifier: Modifier = Modifier) {
    val cs = MaterialTheme.colorScheme
    val files = c.attachmentsPreview
    val desc = when (files.size) {
        0 -> stringResource(R.string.mail_has_attachments)
        1 -> stringResource(R.string.mail_attachment_named, files[0].filename)
        else -> pluralStringResource(R.plurals.mail_attachments_n, files.size, files.size)
    }
    Row(modifier.semantics(mergeDescendants = true) { contentDescription = desc }, verticalAlignment = Alignment.CenterVertically) {
        Icon(Icons.Outlined.AttachFile, null, Modifier.size(14.dp), tint = cs.onSurfaceVariant)
        val text = if (files.size > 1) "${files.size}" else null
        if (text != null) {
            Text(text, style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant, maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(start = 2.dp))
        }
    }
}

private val SIGNAL_DAY: java.time.format.DateTimeFormatter get() = LocaleFormats.date("MMMd", "MMM d")

/** The chip text and whether it is urgent (a deadline within two days, or past). */
internal fun signalChip(s: io.github.murtazanasir.mailr.data.api.Signal, today: java.time.LocalDate = AppClock.today()): Pair<String, Boolean>? =
    when (s.name) {
        "needs_reply" -> AppText.get(R.string.mail_signal_reply_needed) to false
        "waiting_on" -> AppText.get(R.string.mail_signal_waiting) to false
        "deadline" -> {
            val d = s.date?.let { catching { java.time.LocalDate.parse(it.take(10)) }.getOrNull() }
            if (d == null) AppText.get(R.string.mail_signal_deadline) to false
            else AppText.get(R.string.mail_signal_due, SIGNAL_DAY.format(d)) to (java.time.temporal.ChronoUnit.DAYS.between(today, d) <= 2)
        }
        else -> null
    }

/** Triage signals as small filled bubbles right after the subject (as on the web's list rows). */
@Composable
internal fun SignalChips(signals: List<io.github.murtazanasir.mailr.data.api.Signal>) {
    val cs = MaterialTheme.colorScheme
    signals.forEach { s ->
        val (text, urgent) = signalChip(s) ?: return@forEach
        val (bg, fg) = when {
            s.name == "needs_reply" -> cs.primaryContainer to cs.onPrimaryContainer
            s.name == "deadline" && urgent -> cs.errorContainer to cs.onErrorContainer
            s.name == "deadline" -> cs.tertiaryContainer to cs.onTertiaryContainer
            else -> cs.surfaceContainerHighest to cs.onSurfaceVariant
        }
        Spacer(Modifier.width(6.dp))
        Surface(shape = RoundedCornerShape(50), color = bg) {
            Text(text, style = MaterialTheme.typography.labelSmall, color = fg, maxLines = 1,
                modifier = Modifier.padding(horizontal = 7.dp, vertical = 2.dp))
        }
    }
}

/** The No reply mark, sized like the signal chips beside it. */
@Composable
internal fun NoReplyPill() {
    Surface(shape = RoundedCornerShape(50), color = MaterialTheme.colorScheme.tertiaryContainer) {
        Row(Modifier.padding(horizontal = 7.dp, vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.NotificationsActive, null, Modifier.size(12.dp), tint = MaterialTheme.colorScheme.onTertiaryContainer)
            Spacer(Modifier.width(3.dp))
            Text(stringResource(R.string.mail_no_reply), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onTertiaryContainer,
                maxLines = 1)
        }
    }
}

private data class SwipeLook(val color: Color, val icon: ImageVector, val label: String)

@Composable
private fun look(action: SwipeAction, unread: Boolean, place: MailPlace, slot: ArchiveSlot = ArchiveSlot.ARCHIVE): SwipeLook? = when {
    slot == ArchiveSlot.INBOX && action == SwipeAction.ARCHIVE -> SwipeLook(Mail.colors.swipeArchive, Icons.Outlined.MoveToInbox, stringResource(R.string.mail_move_to_inbox))
    place == MailPlace.TRASH && action == SwipeAction.ARCHIVE -> SwipeLook(Mail.colors.swipeArchive, Icons.Outlined.RestoreFromTrash, stringResource(R.string.mail_restore))
    place == MailPlace.TRASH && action == SwipeAction.TRASH -> SwipeLook(Mail.colors.swipeDelete, Icons.Outlined.DeleteForever, stringResource(R.string.mail_delete_forever))
    place == MailPlace.SPAM && action == SwipeAction.ARCHIVE -> SwipeLook(Mail.colors.swipeArchive, Icons.Outlined.MoveToInbox, stringResource(R.string.mail_not_spam))
    else -> look(action, unread)
}

@Composable
private fun look(action: SwipeAction, unread: Boolean): SwipeLook? = when (action) {
    SwipeAction.ARCHIVE -> SwipeLook(Mail.colors.swipeArchive, Icons.Outlined.Archive, stringResource(R.string.mail_archive))
    SwipeAction.TRASH -> SwipeLook(Mail.colors.swipeDelete, Icons.Outlined.Delete, stringResource(R.string.mail_delete))
    SwipeAction.READ -> SwipeLook(Mail.colors.swipeRead, if (unread) Icons.Outlined.Drafts else Icons.Outlined.MarkEmailUnread,
        if (unread) stringResource(R.string.mail_swipe_read) else stringResource(R.string.mail_swipe_unread))
    SwipeAction.SNOOZE -> SwipeLook(Mail.colors.swipeSnooze, Icons.Outlined.Schedule, stringResource(R.string.mail_snooze))
    SwipeAction.STAR -> SwipeLook(Mail.colors.star, Icons.Filled.Star, stringResource(R.string.mail_star))
    SwipeAction.NONE -> null
}

@Composable
private fun SwipeBackground(direction: SwipeToDismissBoxValue, start: SwipeAction, end: SwipeAction, unread: Boolean,
                            place: MailPlace = MailPlace.OTHER, slot: ArchiveSlot = ArchiveSlot.ARCHIVE) {
    val l = when (direction) {
        SwipeToDismissBoxValue.StartToEnd -> look(start, unread, place, slot)
        SwipeToDismissBoxValue.EndToStart -> look(end, unread, place, slot)
        SwipeToDismissBoxValue.Settled -> null
    } ?: return
    Box(
        Modifier.fillMaxSize().background(l.color).padding(horizontal = 24.dp),
        contentAlignment = if (direction == SwipeToDismissBoxValue.StartToEnd) Alignment.CenterStart else Alignment.CenterEnd,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(l.icon, null, tint = Color.White)
            Spacer(Modifier.width(8.dp))
            Text(l.label, color = Color.White, style = MaterialTheme.typography.labelLarge)
        }
    }
}

/** Only web links are offered; sign-in and verification links never reach here (the server leaves them out). */
val PrimaryAction.openable: Boolean get() = (kind == "code" && !code.isNullOrBlank()) ||
    (label.isNotBlank() && (url.startsWith("https://") || url.startsWith("http://")))

/**
 * ACT mail.primary_action: the chip ("Track package") and its key fact, on list rows and at the
 * top of the reader. The link opens in the browser; the server never fetches it.
 */
@Composable
fun PrimaryActionChip(pa: PrimaryAction, modifier: Modifier = Modifier) {
    val uri = androidx.compose.ui.platform.LocalUriHandler.current
    val context = androidx.compose.ui.platform.LocalContext.current
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = modifier) {
        if (pa.kind == "code" && !pa.code.isNullOrBlank()) {
            // A one-time code: the chip copies it (Android shows its own "copied" confirmation on 13+).
            io.github.murtazanasir.mailr.look.AssistChip(
                onClick = {
                    copySensitive(context, context.getString(R.string.mail_code_label), pa.code)
                    if (android.os.Build.VERSION.SDK_INT < 33)
                        android.widget.Toast.makeText(context, context.getString(R.string.mail_code_copied), android.widget.Toast.LENGTH_SHORT).show()
                },
                label = { Text(stringResource(R.string.mail_code_copy, pa.code), maxLines = 1) },
                leadingIcon = { Icon(androidx.compose.material.icons.Icons.Outlined.ContentCopy, null, Modifier.size(16.dp)) },
                modifier = Modifier.height(30.dp))
        } else io.github.murtazanasir.mailr.look.AssistChip(onClick = { catching { uri.openUri(pa.url) } },
            label = { Text(pa.label, maxLines = 1) },
            modifier = Modifier.height(30.dp))
        pa.fact?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1, overflow = TextOverflow.Ellipsis) }
    }
}

/**
 * Copy a one-time code marked sensitive: Android 13+ hides it in the "copied" preview and
 * keyboards leave it out of their clipboard history. It is cleared again after a minute if it
 * is still what the clipboard holds.
 */
fun copySensitive(context: android.content.Context, label: String, text: String) {
    val cm = context.getSystemService(android.content.ClipboardManager::class.java) ?: return
    val clip = android.content.ClipData.newPlainText(label, text)
    clip.description.extras = android.os.PersistableBundle().apply {
        // ClipDescription.EXTRA_IS_SENSITIVE on API 33+; the same key is honoured by keyboards before that.
        putBoolean("android.content.extra.IS_SENSITIVE", true)
    }
    catching { cm.setPrimaryClip(clip) }
    android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
        catching {
            val now = cm.primaryClip
            if (now != null && now.itemCount > 0 && now.getItemAt(0).text?.toString() == text) {
                if (android.os.Build.VERSION.SDK_INT >= 28) cm.clearPrimaryClip()
                else cm.setPrimaryClip(android.content.ClipData.newPlainText("", ""))
            }
        }
    }, CLEAR_CODE_AFTER_MS)
}

/** How long a copied code stays on the clipboard. */
const val CLEAR_CODE_AFTER_MS = 60_000L
