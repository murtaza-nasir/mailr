package io.github.murtazanasir.mailr.ui.conversation

import io.github.murtazanasir.mailr.util.catching

import androidx.compose.ui.draw.clip

import androidx.compose.material.icons.outlined.Badge

import io.github.murtazanasir.mailr.time.AppClock
import android.net.Uri
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.Forward
import androidx.compose.material.icons.automirrored.outlined.Reply
import androidx.compose.material.icons.automirrored.outlined.ReplyAll
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.outlined.Event
import androidx.compose.material.icons.outlined.Checklist
import androidx.compose.material.icons.outlined.Groups
import androidx.compose.material.icons.outlined.Link
import androidx.compose.material.icons.automirrored.outlined.StickyNote2
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.DeleteForever
import androidx.compose.material.icons.outlined.MoveToInbox
import androidx.compose.material.icons.outlined.RestoreFromTrash
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material.icons.outlined.MarkEmailUnread
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material.icons.outlined.MoreHoriz
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.PictureAsPdf
import androidx.compose.material.icons.outlined.Shield
import androidx.compose.material.icons.outlined.StarOutline
import androidx.compose.material.icons.outlined.Translate
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.material.icons.outlined.Warning
import io.github.murtazanasir.mailr.look.AssistChip
import io.github.murtazanasir.mailr.look.Card
import androidx.compose.material3.CardDefaults
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material.icons.automirrored.outlined.Label
import androidx.compose.material.icons.automirrored.outlined.LabelImportant
import androidx.compose.material.icons.outlined.AlarmAdd
import androidx.compose.material.icons.outlined.Category
import androidx.compose.material.icons.outlined.DriveFileMove
import androidx.compose.material.icons.outlined.Forum
import androidx.compose.material.icons.outlined.NotificationsOff
import androidx.compose.material.icons.outlined.Report
import androidx.compose.material.icons.outlined.Snooze
import androidx.compose.material.icons.outlined.Tab
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import io.github.murtazanasir.mailr.look.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedButton
import androidx.compose.material3.Scaffold
import io.github.murtazanasir.mailr.look.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.Attachment
import io.github.murtazanasir.mailr.data.api.SafeText
import io.github.murtazanasir.mailr.data.api.AssistantRun
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.api.ConversationDetail
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.Message
import io.github.murtazanasir.mailr.data.api.SummaryResponse
import io.github.murtazanasir.mailr.ui.components.ActionSheet
import io.github.murtazanasir.mailr.ui.components.Avatar
import io.github.murtazanasir.mailr.ui.components.SheetAction
import io.github.murtazanasir.mailr.ui.components.SheetGroup
import io.github.murtazanasir.mailr.ui.components.UserAvatar
import io.github.murtazanasir.mailr.ui.components.EmptyState
import io.github.murtazanasir.mailr.ui.components.ImportanceMarker
import io.github.murtazanasir.mailr.ui.components.LabelChip
import io.github.murtazanasir.mailr.ui.components.Shimmer
import io.github.murtazanasir.mailr.ui.mail.DeleteForeverDialog
import io.github.murtazanasir.mailr.ui.mail.ArchiveSlot
import io.github.murtazanasir.mailr.ui.mail.MailPlace
import io.github.murtazanasir.mailr.ui.mail.archiveSlot
import io.github.murtazanasir.mailr.ui.mail.PrimaryActionChip
import io.github.murtazanasir.mailr.ui.mail.openable
import io.github.murtazanasir.mailr.ui.theme.Mail
import io.github.murtazanasir.mailr.ui.util.Format
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.LocaleFormats

data class ConversationActions(
    val back: (() -> Unit)?,
    val act: (String) -> Unit,
    val toggle: (Long) -> Unit,
    val expandAll: () -> Unit,
    val star: (Message, Boolean) -> Unit,
    val reply: (Message, String) -> Unit,
    val openAttachment: (Message, Attachment) -> Unit,
    val showImages: (Message) -> Unit,
    val snooze: () -> Unit,
    val labels: () -> Unit,
    val summarize: () -> Unit,
    val unsubscribe: (Message) -> Unit,
    val mailto: (Uri) -> Unit,
    val retry: () -> Unit,
    val ask: () -> Unit = {},
    /** ACT mail.follow_up @android.reader: set (an ISO time) or cancel (null) "remind me if nobody replies". */
    val followUp: (String?) -> Unit = {},
    /** ACT mail.to_task @android.reader, ACT mail.to_event @android.reader: open the task or event editor on what the email says. */
    val toTask: () -> Unit = {},
    val toEvent: () -> Unit = {},
    val openCitation: (Citation) -> Unit = {},
    val openUrl: (String) -> Unit = {},
    /** ACT event.import_ics @android.reader: an .ics attachment into the default calendar (POST /calendar/import). */
    val importIcs: ((Message, Attachment) -> Unit)? = null,
    /** ACT mail.attachment_share @android.reader: an attachment through the share sheet (long press, or TalkBack's actions). */
    val shareAttachment: ((Message, Attachment) -> Unit)? = null,
    /** ACT mail.attachment_save @android.reader: an attachment into Downloads (long press, or TalkBack's actions). */
    val saveAttachment: ((Message, Attachment) -> Unit)? = null,
    /** ACT agent.ask_about @android.reader: the ask capture about this conversation (voice or typed). */
    val dictate: (() -> Unit)? = null,
    /** ACT mail.show_in_tab @android.reader: the Show in tab sheet (null: the layout has one tab). */
    val showInTab: (() -> Unit)? = null,
    /** ACT mail.category @android.reader: the Category sheet. */
    val category: (() -> Unit)? = null,
    // Reader parity (ReaderExtras.kt):
    /** The invitation card's answers (ACT event.rsvp and the invite.* cells). */
    val invite: InviteCallbacks = InviteCallbacks(),
    /** ACT mail.move @android.reader: the Move to sheet. */
    val moveTo: (() -> Unit)? = null,
    /** ACT note.from_email @android.reader: "text" or "checklist". */
    val noteFromEmail: ((String) -> Unit)? = null,
    /** ACT note.link @android.reader: the Link to a note sheet. */
    val linkNote: (() -> Unit)? = null,
    /** ACT mail.schedule_meeting @android.reader: the event editor with the thread's people as guests. */
    val meeting: (() -> Unit)? = null,
    /** ACT mail.reply_suggestions @android.reader: a suggested reply opens the composer with it. */
    val pickReply: (Message, String) -> Unit = { _, _ -> },
    /** ACT draft.retry_send @android.reader */
    val retrySend: (io.github.murtazanasir.mailr.data.api.OutboxRow) -> Unit = {},
    /** ACT mail.translate @android.reader: translate a message into a language (null hides Translate…). */
    val translate: ((Message, String) -> Unit)? = null,
    /** Show original (false) or the translation (true). */
    val showTranslation: (Message, Boolean) -> Unit = { _, _ -> },
    /** ACT person.card @android.reader: the sender's card (their picture, or ⋮ → Open card); null hides it. */
    val openPerson: ((String) -> Unit)? = null,
    /** ACT mail.open_attached_email @android.reader: load the attached email at a part (images: null = the phone's choice). */
    val openAttachedEmail: (Message, Int, Boolean?) -> Unit = { _, _, _ -> },
)

/** An attachment the calendar can import: an .ics file or a text/calendar part. */
fun Attachment.isIcs(): Boolean =
    filename.endsWith(".ics", ignoreCase = true) || type.substringBefore(';').trim().equals("text/calendar", ignoreCase = true)

@Composable
fun ConversationScreen(
    detail: ConversationDetail?,
    error: String?,
    labels: List<Label>,
    expanded: Set<Long>,
    bodies: Map<Long, BodyState>,
    summary: SummaryResponse?,
    quote: String?,
    tabletop: Boolean,
    snackbar: SnackbarHostState,
    actions: ConversationActions,
    brief: AssistantRun? = null,
    assistantName: String = io.github.murtazanasir.mailr.data.api.AssistantGate.DEFAULT_NAME,
    /** In Spam: Not spam; in Trash: Restore and Delete forever; neither offers Report spam. */
    place: MailPlace = MailPlace.OTHER,
    /** Invitations, suggested replies and send states (ReaderExtras.kt). */
    extras: ReaderExtrasUi = ReaderExtrasUi(),
    /** The label of the list the reader was opened from; Archive there also removes it ([archiveSlot]). */
    contextLabelId: Long? = null,
    /** archive_removes_current_label (on by default). */
    archiveRemovesLabel: Boolean = true,
) {
    val labelMap = remember(labels) { labels.associateBy { it.id } }
    // Archive, or Move to Inbox when the conversation is not in the Inbox.
    val slot = detail?.let { archiveSlot(listOf(it.conversation), place, labelMap, contextLabelId, archiveRemovesLabel) } ?: ArchiveSlot.ARCHIVE
    var menu by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }
    var followUpPicker by remember { mutableStateOf(false) }
    var showAll by rememberSaveable { mutableStateOf(false) }
    val wroteHere = detail?.messages?.any { it.sentByMe && !it.draft } == true
    Scaffold(
        contentWindowInsets = WindowInsets(0),
        topBar = {
            TopAppBar(
                title = {},
                navigationIcon = { actions.back?.let { IconButton(onClick = it) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.conv_back)) } } },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.surface),
                actions = {
                    // Reader toolbar (android.reader): ACT mail.archive @android.reader, ACT mail.delete @android.reader; the menu below holds
                    // ACT mail.snooze @android.reader, ACT mail.to_task, ACT mail.to_event, ACT mail.label @android.reader and ACT mail.spam @android.reader.
                    // In Spam: ACT mail.not_spam @android.reader; in Trash: ACT mail.untrash @android.reader, ACT mail.delete_forever @android.reader.
                    when (place) {
                        MailPlace.TRASH -> {
                            IconButton(onClick = { actions.act("untrash") }) { Icon(Icons.Outlined.RestoreFromTrash, stringResource(R.string.conv_restore)) }
                            IconButton(onClick = { confirmDelete = true }) { Icon(Icons.Outlined.DeleteForever, stringResource(R.string.conv_delete_forever)) }
                        }
                        MailPlace.SPAM -> {
                            IconButton(onClick = { actions.act("not_spam") }) { Icon(Icons.Outlined.MoveToInbox, stringResource(R.string.conv_not_spam)) }
                            IconButton(onClick = { actions.act("trash") }) { Icon(Icons.Outlined.Delete, stringResource(R.string.conv_delete)) }
                        }
                        MailPlace.OTHER -> {
                            // ACT mail.move @android.reader (Move to Inbox)
                            if (slot == ArchiveSlot.INBOX) IconButton(onClick = { actions.act("inbox") }) { Icon(Icons.Outlined.MoveToInbox, stringResource(R.string.mail_move_to_inbox)) }
                            else IconButton(onClick = { actions.act("archive") }) { Icon(Icons.Outlined.Archive, stringResource(R.string.conv_archive)) }
                            IconButton(onClick = { actions.act("trash") }) { Icon(Icons.Outlined.Delete, stringResource(R.string.conv_delete)) }
                        }
                    }
                    IconButton(onClick = { actions.act("unread") }) { Icon(Icons.Outlined.MarkEmailUnread, stringResource(R.string.conv_mark_unread)) }
                    actions.dictate?.let { d -> IconButton(onClick = d) { Icon(Icons.Outlined.Mic, stringResource(R.string.conv_ask_ambrose_about, io.github.murtazanasir.mailr.ui.assistantName())) } }
                    IconButton(onClick = { menu = true }) { Icon(Icons.Outlined.MoreVert, stringResource(R.string.conv_more)) }
                    // The ⋮ menu as a bottom sheet in groups (a plain menu ran past the
                    // bottom of the screen): the common actions first, then Create from email, Organize and More.
                    if (menu) {
                        val other = place == MailPlace.OTHER
                        ActionSheet(detail?.conversation?.subject?.takeIf { it.isNotBlank() }, listOf(
                            SheetGroup(null, listOfNotNull(
                                SheetAction(stringResource(R.string.conv_summarize), Icons.Outlined.AutoAwesome) { actions.summarize() },
                                SheetAction(stringResource(R.string.conv_ask_ambrose, io.github.murtazanasir.mailr.ui.assistantName()), Icons.Outlined.Forum) { actions.ask() },
                                if (other) SheetAction(stringResource(R.string.conv_snooze), Icons.Outlined.Snooze) { actions.snooze() } else null,
                                if (wroteHere) SheetAction(
                                    if (detail?.conversation?.followUpAt != null) stringResource(R.string.conv_follow_up_reminder) else stringResource(R.string.conv_remind_if_no_reply),
                                    Icons.Outlined.AlarmAdd) { followUpPicker = true } else null,
                                // ACT mail.important @android.reader
                                if (detail?.conversation?.important == true)
                                    SheetAction(stringResource(R.string.conv_mark_not_important), Icons.AutoMirrored.Outlined.LabelImportant) { actions.act("not_important") }
                                else SheetAction(stringResource(R.string.conv_mark_important), Icons.AutoMirrored.Outlined.LabelImportant) { actions.act("important") },
                            )),
                            // Create from email: the web's menu, with the entries Android has.
                            SheetGroup(stringResource(R.string.conv_create_from_email), if (detail == null) emptyList() else listOfNotNull(
                                SheetAction(stringResource(R.string.conv_create_task), Icons.Outlined.TaskAlt) { actions.toTask() },
                                SheetAction(stringResource(R.string.conv_create_event), Icons.Outlined.Event) { actions.toEvent() },
                                actions.meeting?.let { SheetAction(stringResource(R.string.conv_create_meeting), Icons.Outlined.Groups, onClick = it) },
                                actions.noteFromEmail?.let { f -> SheetAction(stringResource(R.string.conv_create_note), Icons.AutoMirrored.Outlined.StickyNote2) { f("text") } },
                                actions.noteFromEmail?.let { f -> SheetAction(stringResource(R.string.conv_create_checklist), Icons.Outlined.Checklist) { f("checklist") } },
                                actions.linkNote?.let { SheetAction(stringResource(R.string.conv_link_note), Icons.Outlined.Link, onClick = it) },
                            )),
                            SheetGroup(stringResource(R.string.ui_menu_organize), listOfNotNull(
                                actions.moveTo?.let { SheetAction(stringResource(R.string.conv_move_to), Icons.Outlined.DriveFileMove, onClick = it) },
                                SheetAction(stringResource(R.string.conv_label_as), Icons.AutoMirrored.Outlined.Label) { actions.labels() },
                                if (other) actions.showInTab?.let { SheetAction(stringResource(R.string.conv_show_in_tab), Icons.Outlined.Tab, onClick = it) } else null,
                                if (other) actions.category?.let { SheetAction(stringResource(R.string.conv_category), Icons.Outlined.Category, onClick = it) } else null,
                            )),
                            // ACT mail.mute @android.reader
                            SheetGroup(stringResource(R.string.ui_menu_more), if (!other) emptyList() else listOf(
                                SheetAction(stringResource(R.string.conv_mute), Icons.Outlined.NotificationsOff) { actions.act("mute") },
                                SheetAction(stringResource(R.string.conv_report_spam), Icons.Outlined.Report) { actions.act("spam") },
                            )),
                        ), onDismiss = { menu = false })
                    }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { pad ->
        if (confirmDelete) DeleteForeverDialog(1, { confirmDelete = false }) {
            confirmDelete = false
            actions.act("delete_forever")
        }
        if (followUpPicker) FollowUpDialog(detail?.conversation?.followUpAt, { followUpPicker = false }) { until ->
            followUpPicker = false
            actions.followUp(until)
        }
        Box(Modifier.padding(pad).fillMaxSize()) {
            when {
                detail == null && error != null -> EmptyState(Icons.Outlined.Warning, stringResource(R.string.conv_cant_open), error,
                    action = stringResource(R.string.conv_try_again) to actions.retry)
                detail == null -> LoadingThread()
                tabletop -> Column(Modifier.fillMaxSize()) {
                    Box(Modifier.weight(1f)) { Thread(detail, labelMap, expanded, bodies, summary, quote, showAll, { showAll = true }, actions, brief, assistantName, extras) }
                    TabletopActions(detail.messages.lastOrNull(), actions, Modifier.weight(1f), place, slot) { confirmDelete = true }
                }
                else -> Thread(detail, labelMap, expanded, bodies, summary, quote, showAll, { showAll = true }, actions, brief, assistantName, extras)
            }
        }
    }
}

@Composable
private fun LoadingThread() {
    Column(Modifier.fillMaxWidth().padding(20.dp)) {
        Shimmer(Modifier.fillMaxWidth(0.7f).height(26.dp))
        Spacer(Modifier.height(24.dp))
        repeat(3) {
            Row { Shimmer(Modifier.size(40.dp)); Spacer(Modifier.width(12.dp)); Column {
                Shimmer(Modifier.fillMaxWidth(0.4f).height(14.dp)); Spacer(Modifier.height(8.dp))
                Shimmer(Modifier.fillMaxWidth(0.9f).height(12.dp)) } }
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun Thread(
    d: ConversationDetail,
    labels: Map<Long, Label>,
    expanded: Set<Long>,
    bodies: Map<Long, BodyState>,
    summary: SummaryResponse?,
    quote: String?,
    showAll: Boolean,
    onShowAll: () -> Unit,
    actions: ConversationActions,
    brief: AssistantRun?,
    assistantName: String,
    extras: ReaderExtrasUi = ReaderExtrasUi(),
) {
    val msgs = d.messages
    // Gmail collapses long threads to: first, "N more", the last two (plus anything expanded).
    val hidden = if (!showAll && msgs.size > 4) msgs.subList(1, msgs.size - 2).filter { it.id !in expanded } else emptyList()
    val hiddenIds = hidden.map { it.id }.toSet()
    val state = rememberLazyListState()
    LazyColumn(state = state, contentPadding = PaddingValues(bottom = 24.dp), modifier = Modifier.fillMaxSize()) {
        item(key = "subject") { SubjectHeader(d, labels, actions) }
        if (d.conversation.nudgedAt != null) item(key = "nudge") { NoReplyBanner(d, actions) }
        if (brief != null) item(key = "brief") { BriefCard(brief, assistantName, actions.openCitation, actions.openUrl) }
        if (summary != null) item(key = "summary") { SummaryCard(summary) }
        if (quote != null) item(key = "quote") { QuoteBanner(quote) }
        var bubbleShown = false
        msgs.forEach { m ->
            if (m.id in hiddenIds) {
                if (!bubbleShown) {
                    bubbleShown = true
                    item(key = "more") { MoreBubble(hidden.size, onShowAll) }
                }
                return@forEach
            }
            item(key = m.id) {
                MessageItem(m, m.id in expanded, bodies[m.id], labels, actions, extras)
            }
        }
        // ACT mail.reply_suggestions: under the newest message from someone else.
        extras.replies?.let { (mid, replies) ->
            val m = msgs.lastOrNull { !it.draft }
            if (m != null && m.id == mid && replies.isNotEmpty()) item(key = "reply-chips") { ReplyChips(replies) { actions.pickReply(m, it) } }
        }
        // ACT mail.reply @android.reader, ACT mail.forward @android.reader: opens the composer with ComposeArgs(replyToMessageId, mode).
        item(key = "reply") {
            val last = msgs.lastOrNull { !it.draft } ?: return@item
            val all = (last.to.size + last.cc.size) > 1
            // Large text: one button per line, so Reply and Reply all never shrink to "Re…".
            if (LocalDensity.current.fontScale > 1.3f) {
                Column(Modifier.fillMaxWidth().padding(16.dp).navigationBarsPadding(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    ReplyButton(Icons.AutoMirrored.Outlined.Reply, stringResource(R.string.conv_reply), Modifier.fillMaxWidth()) { actions.reply(last, "reply") }
                    if (all) ReplyButton(Icons.AutoMirrored.Outlined.ReplyAll, stringResource(R.string.conv_reply_all), Modifier.fillMaxWidth()) { actions.reply(last, "reply_all") }
                    ReplyButton(Icons.AutoMirrored.Outlined.Forward, stringResource(R.string.conv_forward), Modifier.fillMaxWidth()) { actions.reply(last, "forward") }
                }
                return@item
            }
            Row(Modifier.fillMaxWidth().padding(16.dp).navigationBarsPadding(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                ReplyButton(Icons.AutoMirrored.Outlined.Reply, stringResource(R.string.conv_reply), Modifier.weight(1f)) { actions.reply(last, "reply") }
                if (all) ReplyButton(Icons.AutoMirrored.Outlined.ReplyAll, stringResource(R.string.conv_reply_all), Modifier.weight(1f)) { actions.reply(last, "reply_all") }
                ReplyButton(Icons.AutoMirrored.Outlined.Forward, stringResource(R.string.conv_forward), Modifier.weight(1f)) { actions.reply(last, "forward") }
            }
        }
    }
}

@Composable
private fun ReplyButton(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, modifier: Modifier, onClick: () -> Unit) {
    OutlinedButton(onClick = onClick, modifier = modifier.heightIn(min = 48.dp), contentPadding = PaddingValues(horizontal = 10.dp, vertical = 10.dp)) {
        Icon(icon, null, Modifier.size(20.dp))
        Spacer(Modifier.width(6.dp))
        Text(label, maxLines = 1, softWrap = false, overflow = TextOverflow.Ellipsis)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SubjectHeader(d: ConversationDetail, labels: Map<Long, Label>, actions: ConversationActions) {
    val c = d.conversation
    Column(Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp, top = 4.dp, bottom = 8.dp)) {
        Row(verticalAlignment = Alignment.Top) {
            Text(c.subject.ifBlank { stringResource(R.string.conv_no_subject) }, style = MaterialTheme.typography.headlineSmall,
                modifier = Modifier.weight(1f).semantics { heading() })
            ImportanceMarker(c.important, Modifier.padding(top = 10.dp, start = 6.dp), size = 16.dp)
            IconButton(onClick = { actions.star(d.messages.last(), !c.starred) }) {
                if (c.starred) Icon(Icons.Filled.Star, stringResource(R.string.conv_unstar), tint = Mail.colors.star)
                else Icon(Icons.Outlined.StarOutline, stringResource(R.string.conv_star))
            }
        }
        // ACT mail.primary_action @android.reader: the same chip as on the list row, at the top of the reader.
        c.primaryAction?.takeIf { it.openable }?.let { pa ->
            PrimaryActionChip(pa, Modifier.padding(end = 12.dp, bottom = 8.dp))
        }
        val shown = c.labelIds.mapNotNull { labels[it] }.filter { it.role == null || it.role == "inbox" }
        if (shown.isNotEmpty()) {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                shown.forEach { LabelChip(it) }
            }
        }
    }
}

@Composable
private fun SummaryCard(s: SummaryResponse) {
    Card(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer)) {
        Column(Modifier.padding(16.dp).animateContentSize()) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.AutoAwesome, null, Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text(stringResource(R.string.conv_summary), style = MaterialTheme.typography.titleSmall)
            }
            Spacer(Modifier.height(8.dp))
            when (s.status) {
                "pending" -> LinearProgressIndicator(Modifier.fillMaxWidth())
                "ready" -> {
                    s.lines.forEach { Text(stringResource(R.string.conv_summary_bullet, it), style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(vertical = 2.dp)) }
                    if (s.actionItems.isNotEmpty()) {
                        Spacer(Modifier.height(8.dp))
                        Text(stringResource(R.string.conv_summary_todo), style = MaterialTheme.typography.labelLarge)
                        s.actionItems.forEach { Text(stringResource(R.string.conv_summary_todo_item, it), style = MaterialTheme.typography.bodyMedium) }
                    }
                }
                else -> Text(s.reason ?: stringResource(R.string.conv_summary_unavailable),
                    style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
}

@Composable
private fun QuoteBanner(q: String) {
    // In the web's look the colours of the web's quote mark (features/conversation/highlight.ts mark.mailr-hl),
    // dark in the dark scheme; Material's look keeps its yellow in both.
    val web = io.github.murtazanasir.mailr.look.look
    val bg = if (web.web && web.dark) Color(0xFF6B5A1E) else Color(0xFFFDE68A)
    val fg = if (web.web && web.dark) Color(0xFFFFF7D6) else Color(0xFF1F1F1F)
    Surface(color = bg, contentColor = fg, shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp)) {
        Text(stringResource(R.string.conv_highlighted, q), style = MaterialTheme.typography.bodySmall, maxLines = 3, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(12.dp))
    }
}

@Composable
private fun MoreBubble(n: Int, onClick: () -> Unit) {
    Box(Modifier.fillMaxWidth().padding(vertical = 4.dp), contentAlignment = Alignment.Center) {
        HorizontalDivider(Modifier.fillMaxWidth())
        Surface(shape = CircleShape, color = MaterialTheme.colorScheme.surfaceContainerHigh,
            border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
            modifier = Modifier.clickable(onClickLabel = pluralStringResource(R.plurals.conv_show_more_messages, n, n)) { onClick() }) {
            Text(stringResource(R.string.conv_n_more, n), style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp))
        }
    }
}

@Composable
private fun MessageItem(m: Message, open: Boolean, body: BodyState?, labels: Map<Long, Label>, actions: ConversationActions,
                        extras: ReaderExtrasUi = ReaderExtrasUi()) {
    val cs = MaterialTheme.colorScheme
    // Translate…: the language picker for this message.
    var picking by remember(m.id) { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth().animateContentSize()) {
        HorizontalDivider(color = cs.outlineVariant.copy(alpha = 0.5f))
        Row(
            Modifier.fillMaxWidth().clickable(onClickLabel = if (open) stringResource(R.string.conv_collapse_message) else stringResource(R.string.conv_expand_message)) { actions.toggle(m.id) }
                .padding(start = 16.dp, end = 4.dp, top = 12.dp, bottom = if (open) 4.dp else 12.dp),
            verticalAlignment = Alignment.Top,
        ) {
            val person = actions.openPerson?.takeIf { !m.sentByMe && m.from.email.isNotBlank() }
            if (m.sentByMe) UserAvatar(m.from.name, m.from.email, size = 40.dp)
            else Avatar(m.from.name, m.from.email, size = 40.dp, modifier = if (person == null) Modifier
                else Modifier.clip(CircleShape).clickable(onClickLabel = stringResource(R.string.memory_open_card_of, m.from.display)) { person(m.from.email) }
                    .testTag("sender-avatar"))
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    // Name and Draft share one weighted row so the date sits at the end.
                    Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically) {
                        val name = @Composable { mod: Modifier ->
                            Text(if (m.sentByMe) stringResource(R.string.conv_me) else m.from.display, style = MaterialTheme.typography.titleSmall,
                                fontWeight = if (!m.seen) FontWeight.Bold else FontWeight.Medium,
                                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = mod)
                        }
                        // ACT mail.copy_address @android.reader: a long-press on the open message's sender copies (CopyAddress.kt).
                        if (open && m.from.email.isNotBlank()) CopyableAddress(m.from, onClick = { actions.toggle(m.id) },
                            modifier = Modifier.weight(1f, fill = false).testTag("sender-name")) { name(Modifier) }
                        else name(Modifier.weight(1f, fill = false))
                        if (m.draft) Text(stringResource(R.string.conv_draft_tag), style = MaterialTheme.typography.labelMedium, color = cs.error)
                    }
                    Spacer(Modifier.width(8.dp))
                    Text(Format.listDate(m.date), style = MaterialTheme.typography.labelMedium, color = cs.onSurfaceVariant,
                        modifier = Modifier.padding(end = if (open) 0.dp else 12.dp))
                }
                if (open) {
                    var details by remember { mutableStateOf(false) }
                    val me = stringResource(R.string.conv_me)
                    val undisclosed = stringResource(R.string.conv_undisclosed_recipients)
                    Text(
                        stringResource(R.string.conv_to_recipients, (m.to + m.cc).joinToString { if (it.email == m.from.email) me else Format.firstName(it.display) }.ifBlank { undisclosed }) +
                            if (details) "" else "  ▾",
                        style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant, maxLines = if (details) 10 else 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.clickable(onClickLabel = stringResource(R.string.conv_show_details)) { details = !details },
                    )
                    if (details) Details(m) { actions.toggle(m.id) }
                    if (m.listUnsubscribe != null) {
                        Text(stringResource(R.string.conv_unsubscribe), style = MaterialTheme.typography.labelLarge, color = cs.primary,
                            modifier = Modifier.padding(top = 6.dp).clickable(onClickLabel = stringResource(R.string.conv_unsubscribe_sender)) { actions.unsubscribe(m) })
                    }
                } else {
                    Text(Format.snippet(m.snippet), style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant, maxLines = 1,
                        overflow = TextOverflow.Ellipsis)
                }
            }
            if (open) {
                IconButton(onClick = { actions.star(m, !m.flagged) }) {
                    if (m.flagged) Icon(Icons.Filled.Star, stringResource(R.string.conv_unstar_message), tint = Mail.colors.star)
                    else Icon(Icons.Outlined.StarOutline, stringResource(R.string.conv_star_message), tint = cs.onSurfaceVariant)
                }
                MessageMenu(m, actions) { picking = true }
            }
        }
        if (picking) TranslatePicker(extras.translateTo, onDismiss = { picking = false }) { lang ->
            picking = false
            actions.translate?.invoke(m, lang)
        }
        if (open) {
            if (m.authResults["dmarc"]?.lowercase() in setOf("fail", "reject", "quarantine")) {
                Surface(color = cs.errorContainer, contentColor = cs.onErrorContainer, shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp)) {
                    Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.Warning, null, Modifier.size(18.dp)); Spacer(Modifier.width(8.dp))
                        Text(stringResource(R.string.conv_failed_verification),
                            style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
            // A Speakr notification email: the recording card (Recordings (Speakr) on only).
            if (!m.sentByMe && !m.draft) io.github.murtazanasir.mailr.ui.recordings.LocalRecordings.current?.let { rec ->
                io.github.murtazanasir.mailr.ui.recordings.RecordingMailCard(rec, m.id)
            }
            // ACT invite.view: the invitation card above the message it came in.
            extras.invites[m.id]?.takeIf { !m.sentByMe }?.let { InviteCard(m, it, m.id in extras.busy, actions.invite) }
            // ACT event.change_suggestion @android.reader: a move this email announces in words, under the invitation card.
            m.eventChange?.takeIf { !m.sentByMe && m.id !in extras.changesDone }?.let {
                EventChangeCard(m, it, m.id in extras.changesBusy, actions.invite)
            }
            sendStateOf(m, extras.outbox)?.let { SendStateLine(it, actions.retrySend) }
            val translation = extras.translations[m.id]
            if (actions.translate != null) {
                TranslateBar(translation, offer = m.offerTranslate && body is BodyState.Ready, onOffer = { picking = true },
                    onShow = { on -> actions.showTranslation(m, on) },
                    onRetry = { translation?.let { actions.translate.invoke(m, it.to) } })
            }
            if (translation is Translation.Ready && translation.showing) TranslatedBody(translation.result)
            else BodyView(m, body, actions)
            // Attached emails are cards (AttachedEmails.kt); a broken one stays a plain .eml file.
            AttachedEmailCards(m, extras.attached, actions)
            val files = m.attachments.filter { !it.inline && it.email == null }
            if (files.isNotEmpty()) Attachments(m, files, actions)
            Spacer(Modifier.height(8.dp))
        }
    }
}

@Composable
private fun Details(m: Message, onClick: () -> Unit) {
    val style = MaterialTheme.typography.bodySmall
    val c = MaterialTheme.colorScheme.onSurfaceVariant
    Column(Modifier.padding(top = 4.dp)) {
        // Each part isolated: right-to-left text in a name cannot reorder the address after it.
        // A long-press on a line's address copies it (CopyAddress.kt).
        CopyableAddress(m.from, onClick, Modifier.testTag("detail-from")) {
            Text(stringResource(R.string.conv_detail_from, SafeText.isolate(m.from.display), SafeText.isolate(m.from.email)), style = style, color = c)
        }
        if (m.to.isNotEmpty()) AddressLine(R.string.conv_detail_to, m.to, onClick)
        if (m.cc.isNotEmpty()) AddressLine(R.string.conv_detail_cc, m.cc, onClick)
        Text(stringResource(R.string.conv_detail_date, Format.messageDate(m.date)), style = style, color = c)
        if (m.authResults.isNotEmpty()) Text(stringResource(R.string.conv_detail_security, m.authResults.entries.joinToString { "${it.key}=${it.value}" }), style = style, color = c)
    }
}

/** "to a@example.com, b@example.com": each address its own long-press target. The label keeps
 *  its place in the translated sentence ("to %1$s"). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AddressLine(@androidx.annotation.StringRes res: Int, list: List<io.github.murtazanasir.mailr.data.api.Address>, onClick: () -> Unit) {
    val style = MaterialTheme.typography.bodySmall
    val c = MaterialTheme.colorScheme.onSurfaceVariant
    val parts = stringResource(res, "\u0000").split("\u0000")
    FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        parts.first().trim().takeIf { it.isNotEmpty() }?.let { Text(it, style = style, color = c) }
        list.forEachIndexed { i, a ->
            CopyableAddress(a, onClick) {
                Text(SafeText.isolate(a.email) + if (i < list.lastIndex) "," else "", style = style, color = c)
            }
        }
        parts.getOrNull(1)?.trim()?.takeIf { it.isNotEmpty() }?.let { Text(it, style = style, color = c) }
    }
}

@Composable
private fun MessageMenu(m: Message, actions: ConversationActions, onTranslate: () -> Unit) {
    var open by remember { mutableStateOf(false) }
    IconButton(onClick = { open = true }) { Icon(Icons.Outlined.MoreHoriz, stringResource(R.string.conv_message_actions)) }
    DropdownMenu(open, { open = false }) {
        DropdownMenuItem({ Text(stringResource(R.string.conv_reply)) }, { open = false; actions.reply(m, "reply") }, leadingIcon = { Icon(Icons.AutoMirrored.Outlined.Reply, null) })
        actions.openPerson?.takeIf { !m.sentByMe && m.from.email.isNotBlank() }?.let { person ->
            DropdownMenuItem({ Text(stringResource(R.string.memory_open_card)) }, { open = false; person(m.from.email) },
                leadingIcon = { Icon(Icons.Outlined.Badge, null) })
        }
        DropdownMenuItem({ Text(stringResource(R.string.conv_reply_all)) }, { open = false; actions.reply(m, "reply_all") }, leadingIcon = { Icon(Icons.AutoMirrored.Outlined.ReplyAll, null) })
        DropdownMenuItem({ Text(stringResource(R.string.conv_forward)) }, { open = false; actions.reply(m, "forward") }, leadingIcon = { Icon(Icons.AutoMirrored.Outlined.Forward, null) })
        if (actions.translate != null && !m.draft) DropdownMenuItem({ Text(stringResource(R.string.conv_translate)) }, { open = false; onTranslate() },
            leadingIcon = { Icon(Icons.Outlined.Translate, null) }, modifier = Modifier.testTag("message-translate"))
    }
}

@Composable
private fun BodyView(m: Message, body: BodyState?, actions: ConversationActions) {
    var showQuote by rememberSaveable(m.id) { mutableStateOf(false) }
    val dark = MaterialTheme.colorScheme.surface.luminanceIsDark()
    when (body) {
        null, BodyState.Loading -> Column(Modifier.padding(start = 16.dp, end = 16.dp, top = 8.dp)) {
            Shimmer(Modifier.fillMaxWidth().height(12.dp)); Spacer(Modifier.height(8.dp))
            Shimmer(Modifier.fillMaxWidth(0.8f).height(12.dp)); Spacer(Modifier.height(8.dp))
            Shimmer(Modifier.fillMaxWidth(0.6f).height(12.dp))
        }
        is BodyState.Failed -> Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(body.message, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error,
                modifier = Modifier.weight(1f))
            TextButton(onClick = { actions.toggle(m.id); actions.toggle(m.id) }) { Text(stringResource(R.string.conv_retry)) }
        }
        is BodyState.Ready -> Column {
            // ACT mail.show_images @android.reader: images come through mailr's image proxy; Spam asks first.
            var confirmSpam by remember(m.id) { mutableStateOf(false) }
            if (body.body.remoteImages && !body.imagesAllowed) {
                Surface(color = MaterialTheme.colorScheme.surfaceContainerHigh, shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp)) {
                    Row(Modifier.padding(start = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.Image, null, Modifier.size(18.dp)); Spacer(Modifier.width(8.dp))
                        Text(if (body.body.junk) stringResource(R.string.conv_images_hidden_spam) else stringResource(R.string.conv_images_hidden),
                            style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                        TextButton(onClick = { if (body.body.junk) confirmSpam = true else actions.showImages(m) }) { Text(stringResource(R.string.conv_show_images)) }
                    }
                }
            }
            if (confirmSpam) {
                AlertDialog(
                    onDismissRequest = { confirmSpam = false },
                    title = { Text(stringResource(R.string.conv_load_images_title)) },
                    text = { Text(stringResource(R.string.conv_load_images_text)) },
                    confirmButton = { TextButton(onClick = { confirmSpam = false; actions.showImages(m) }) { Text(stringResource(R.string.conv_load_images)) } },
                    dismissButton = { TextButton(onClick = { confirmSpam = false }) { Text(stringResource(R.string.conv_cancel)) } },
                )
            }
            TrackersBlocked(body.body)
            val html = body.body.html ?: Format.textToHtml(body.body.text.orEmpty())
            // HTML mail sits on a light "paper" card in dark mode so its own colours stay legible.
            val paper = dark && body.body.source == "html"
            Surface(
                color = if (paper) Color(0xFFF8F9FA) else Color.Transparent,
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.fillMaxWidth().padding(horizontal = if (paper) 12.dp else 16.dp),
            ) {
                MessageWebView(html, showQuote, darkText = !dark || paper, allowImages = body.imagesAllowed,
                    onMailto = actions.mailto, modifier = Modifier.padding(if (paper) 12.dp else 0.dp))
            }
            if (body.body.hasQuote) {
                Surface(shape = RoundedCornerShape(8.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh,
                    modifier = Modifier.padding(start = 16.dp, top = 4.dp)
                        .clickable(onClickLabel = if (showQuote) stringResource(R.string.conv_hide_quoted) else stringResource(R.string.conv_show_quoted)) { showQuote = !showQuote }) {
                    Text("•••", modifier = Modifier.padding(horizontal = 10.dp, vertical = 2.dp), style = MaterialTheme.typography.labelLarge)
                }
            }
        }
    }
}

/** "N trackers blocked": open-tracking images the server removed; a tap lists their hosts. */
@Composable
internal fun TrackersBlocked(body: io.github.murtazanasir.mailr.data.api.MessageBody) {
    val count = body.trackers.count
    val links = body.linksUnwrapped
    if (count == 0 && links == 0) return
    var open by remember { mutableStateOf(false) }
    val label = if (count > 0) BodyRequests.trackersLabel(count) else pluralStringResource(R.plurals.conv_tracking_links_removed, links, links)
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp), horizontalArrangement = Arrangement.End) {
        Surface(shape = RoundedCornerShape(50), color = MaterialTheme.colorScheme.surfaceContainerHigh,
            modifier = Modifier.clickable(onClickLabel = stringResource(R.string.conv_show_trackers)) { open = true }) {
            Row(Modifier.padding(horizontal = 10.dp, vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.Shield, null, Modifier.size(14.dp)); Spacer(Modifier.width(4.dp))
                Text(label, style = MaterialTheme.typography.labelMedium)
            }
        }
    }
    if (open) {
        AlertDialog(
            onDismissRequest = { open = false },
            title = { Text(label) },
            text = {
                Column {
                    if (count > 0) {
                        Text(pluralStringResource(R.plurals.conv_trackers_removed_images, count, count))
                        Spacer(Modifier.height(6.dp))
                        body.trackers.domains.forEach { Text(stringResource(R.string.conv_summary_bullet, it), style = MaterialTheme.typography.bodySmall) }
                    }
                    if (links > 0) {
                        Spacer(Modifier.height(6.dp))
                        Text(pluralStringResource(R.plurals.conv_links_redirect, links, links))
                    }
                }
            },
            confirmButton = { TextButton(onClick = { open = false }) { Text(stringResource(R.string.conv_close)) } },
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Attachments(m: Message, files: List<Attachment>, actions: ConversationActions) {
    FlowRow(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        files.forEach { a ->
          var menu by remember { mutableStateOf(false) }
          val shareLabel = stringResource(R.string.conv_attachment_share)
          val saveLabel = stringResource(R.string.conv_attachment_save)
          val more = actions.shareAttachment != null || actions.saveAttachment != null
          Box(
            if (!more) Modifier else Modifier
                // A long press opens Open, Share and Save to Downloads; a tap still opens the file.
                .pointerInput(a) {
                    awaitEachGesture {
                        awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial)
                        val up = withTimeoutOrNull(viewConfiguration.longPressTimeoutMillis) {
                            waitForUpOrCancellation(PointerEventPass.Initial)
                        }
                        if (up == null) {
                            menu = true
                            do {
                                val e = awaitPointerEvent(PointerEventPass.Initial)
                                e.changes.forEach { it.consume() }
                            } while (e.changes.any { it.pressed })
                        }
                    }
                }
                .semantics {
                    customActions = listOfNotNull(
                        actions.shareAttachment?.let { f -> CustomAccessibilityAction(shareLabel) { f(m, a); true } },
                        actions.saveAttachment?.let { f -> CustomAccessibilityAction(saveLabel) { f(m, a); true } },
                    )
                }
                .testTag("attachment-${a.part}"),
          ) {
            AssistChip(
                onClick = { actions.openAttachment(m, a) },
                label = {
                    Column(Modifier.widthIn(max = 180.dp)) {
                        Text(SafeText.isolate(a.filename), maxLines = 1, overflow = TextOverflow.Ellipsis)
                        val risky = SafeText.riskyExtension(a.filename)
                        if (risky != null) Text(stringResource(R.string.conv_risky_file, risky),
                            style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.error, maxLines = 2)
                        else Text(Format.bytes(a.size), style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                },
                leadingIcon = {
                    // The icon follows the extension too: "invoice.exe" sent as application/pdf is not a PDF.
                    val ext = a.filename.substringAfterLast('.', "").lowercase()
                    Icon(when {
                        SafeText.riskyExtension(a.filename) != null -> Icons.Outlined.Warning
                        a.type == "application/pdf" && ext == "pdf" -> Icons.Outlined.PictureAsPdf
                        a.type.startsWith("image/") && ext in setOf("jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp") -> Icons.Outlined.Image
                        else -> Icons.Outlined.Description
                    }, null)
                },
            )
            if (more) DropdownMenu(menu, { menu = false }) {
                DropdownMenuItem({ Text(stringResource(R.string.conv_attachment_open)) }, { menu = false; actions.openAttachment(m, a) })
                actions.shareAttachment?.let { f -> DropdownMenuItem({ Text(shareLabel) }, { menu = false; f(m, a) }) }
                actions.saveAttachment?.let { f -> DropdownMenuItem({ Text(saveLabel) }, { menu = false; f(m, a) }) }
            }
          }
            // ACT event.import_ics: "Add to calendar" beside an .ics file.
            if (a.isIcs() && actions.importIcs != null) AssistChip(
                onClick = { actions.importIcs.invoke(m, a) },
                label = { Text(stringResource(R.string.conv_add_to_calendar)) },
                leadingIcon = { Icon(Icons.Outlined.Event, null) },
            )
        }
    }
}

@Composable
private fun TabletopActions(last: Message?, actions: ConversationActions, modifier: Modifier,
                            place: MailPlace = MailPlace.OTHER, slot: ArchiveSlot = ArchiveSlot.ARCHIVE,
                            deleteForever: () -> Unit = {}) {
    // Half-opened Fold on a table: reading on top, big targets on the bottom half.
    Surface(color = MaterialTheme.colorScheme.surfaceContainer, modifier = modifier.fillMaxWidth()) {
        Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp),
            horizontalAlignment = Alignment.CenterHorizontally) {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                when (place) {
                    MailPlace.TRASH -> {
                        BigAction(Icons.Outlined.RestoreFromTrash, stringResource(R.string.conv_restore)) { actions.act("untrash") }
                        BigAction(Icons.Outlined.DeleteForever, stringResource(R.string.conv_delete_forever)) { deleteForever() }
                    }
                    MailPlace.SPAM -> {
                        BigAction(Icons.Outlined.MoveToInbox, stringResource(R.string.conv_not_spam)) { actions.act("not_spam") }
                        BigAction(Icons.Outlined.Delete, stringResource(R.string.conv_delete)) { actions.act("trash") }
                    }
                    MailPlace.OTHER -> {
                        if (slot == ArchiveSlot.INBOX) BigAction(Icons.Outlined.MoveToInbox, stringResource(R.string.mail_move_to_inbox)) { actions.act("inbox") }
                        else BigAction(Icons.Outlined.Archive, stringResource(R.string.conv_archive)) { actions.act("archive") }
                        BigAction(Icons.Outlined.Delete, stringResource(R.string.conv_delete)) { actions.act("trash") }
                    }
                }
                BigAction(Icons.Outlined.MarkEmailUnread, stringResource(R.string.conv_unread)) { actions.act("unread") }
            }
            if (last != null) Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                BigAction(Icons.AutoMirrored.Outlined.Reply, stringResource(R.string.conv_reply)) { actions.reply(last, "reply") }
                BigAction(Icons.AutoMirrored.Outlined.ReplyAll, stringResource(R.string.conv_reply_all)) { actions.reply(last, "reply_all") }
                BigAction(Icons.AutoMirrored.Outlined.Forward, stringResource(R.string.conv_forward)) { actions.reply(last, "forward") }
            }
        }
    }
}

@Composable
private fun BigAction(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, onClick: () -> Unit) {
    Surface(onClick = onClick, shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.secondaryContainer) {
        Column(Modifier.size(width = 104.dp, height = 76.dp), horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center) {
            Icon(icon, null)
            Spacer(Modifier.height(4.dp))
            Text(label, style = MaterialTheme.typography.labelLarge)
        }
    }
}

internal fun Color.luminanceIsDark(): Boolean = (0.2126f * red + 0.7152f * green + 0.0722f * blue) < 0.5f


/** mail.follow_up choices: 1, 2, 3 or 7 days at 9:00 (the web's presets); cancel when one is set. */
@Composable
internal fun FollowUpDialog(current: String?, onDismiss: () -> Unit, onPick: (String?) -> Unit) {
    val zone = java.time.ZoneId.systemDefault()
    val choices = listOf(R.string.conv_follow_up_tomorrow to 1L, R.string.conv_follow_up_2_days to 2L, R.string.conv_follow_up_3_days to 3L, R.string.conv_follow_up_week to 7L).map { (label, days) ->
        label to AppClock.today(zone).plusDays(days).atTime(9, 0).atZone(zone)
    }
    val fmt = LocaleFormats.date("EEEdMMMhmma", "EEE d MMM, h:mm a")
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.conv_follow_up_title)) },
        text = {
            Column {
                if (current != null) Text(stringResource(R.string.conv_follow_up_set_for, catching {
                    java.time.OffsetDateTime.parse(current).atZoneSameInstant(zone).format(fmt) }.getOrDefault(current)),
                    style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(bottom = 8.dp))
                choices.forEach { (label, at) ->
                    ListItem(headlineContent = { Text(stringResource(label)) }, supportingContent = { Text(at.format(fmt)) },
                        modifier = Modifier.clickable { onPick(at.toOffsetDateTime().toString()) })
                }
            }
        },
        confirmButton = { if (current != null) TextButton(onClick = { onPick(null) }) { Text(stringResource(R.string.conv_follow_up_cancel)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.conv_close)) } },
    )
}

/** The conversation came back because nobody replied: write a follow-up, or dismiss the mark. */
@Composable
private fun NoReplyBanner(d: ConversationDetail, actions: ConversationActions) {
    val mine = d.messages.lastOrNull { it.sentByMe && !it.draft }
    // conversation.css .nudge-banner: the warning tint in the web's look.
    val (bg, fg) = io.github.murtazanasir.mailr.look.bannerColors(io.github.murtazanasir.mailr.look.BannerTone.Warning,
        MaterialTheme.colorScheme.tertiaryContainer, MaterialTheme.colorScheme.onTertiaryContainer)
    Surface(color = bg, shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp)) {
        Column(Modifier.padding(12.dp)) {
            Text(stringResource(R.string.conv_no_reply_since), color = fg,
                style = MaterialTheme.typography.bodyMedium)
            Row {
                if (mine != null) TextButton(onClick = { actions.reply(mine, "reply_all") }) { Text(stringResource(R.string.conv_write_follow_up)) }
                TextButton(onClick = { actions.followUp(null) }) { Text(stringResource(R.string.conv_dismiss)) }
            }
        }
    }
}
