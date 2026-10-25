package io.github.murtazanasir.mailr.ui.assistant

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowRight
import androidx.compose.material.icons.outlined.ArrowDropDown
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.FilterAlt
import androidx.compose.material.icons.outlined.FindInPage
import androidx.compose.material.icons.outlined.Pause
import androidx.compose.material.icons.outlined.PlayArrow
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.ReviewGroup
import io.github.murtazanasir.mailr.data.api.ReviewConversation
import io.github.murtazanasir.mailr.data.api.ReviewLabel
import io.github.murtazanasir.mailr.data.api.ReviewPreview
import io.github.murtazanasir.mailr.data.api.ReviewSender
import io.github.murtazanasir.mailr.data.api.ReviewState
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import androidx.compose.ui.semantics.contentDescription
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.text.NumberFormat
import io.github.murtazanasir.mailr.util.catching
import java.time.LocalDate

/*
 * A bulk review in the chat (backend agent/review.py), as the web shows it (ReviewProgress.tsx,
 * ReviewPlan.tsx): the progress line with Stop and Continue while the review runs, and the body of
 * its review card, the proposed moves by sender with a move menu per sender, conversation and kept
 * or unsure row. The polling and the card's hand-over are in AssistantViewModel (followReview); the
 * card itself is ApprovalCard (ui/more), which holds the user's changes and sends them with Apply.
 * ACT agent.review_mail @android.assistant
 */

private fun n(x: Int): String = NumberFormat.getIntegerInstance().format(x)

/** The order of the set-aside reasons on the "Kept:" line (agent/review.py REASONS). */
private val ASIDE = listOf("excepted", "starred", "important", "replied", "known", "invitation")

@Composable
private fun asideText(why: String, count: Int): String = pluralStringResource(when (why) {
    "excepted" -> R.plurals.asst_review_aside_excepted
    "starred" -> R.plurals.asst_review_aside_starred
    "important" -> R.plurals.asst_review_aside_important
    "replied" -> R.plurals.asst_review_aside_replied
    "known" -> R.plurals.asst_review_aside_known
    "invitation" -> R.plurals.asst_review_aside_invitation
    else -> R.plurals.asst_review_aside_other
}, count, n(count))

/**
 * One review's line in its turn: "Reviewing Inbox (all tabs): 143 conversations" with a bar, "50 of 143
 * checked" and Stop (the exact scope and its size from the start, so a wrong scope can be stopped at
 * once), "Stopping after this batch…", "Review stopped at …" with Continue, the failure with Continue,
 * or the result. The scope's name comes from the server (agent/view_tools.py scope_name).
 */
@Composable
fun ReviewProgressLine(r: ReviewState, acting: Boolean = false, onStop: () -> Unit, onResume: () -> Unit) {
    val head = when (r.status) {
        "running" -> pluralStringResource(R.plurals.asst_review_reviewing, r.total, r.scope, n(r.total))
        "stopping" -> stringResource(R.string.asst_review_stopping)
        "stopped" -> stringResource(R.string.asst_review_stopped, n(r.done), n(r.total))
        "failed" -> stringResource(R.string.asst_review_failed, r.error.orEmpty())
        "applied" -> stringResource(R.string.asst_review_applied)
        else -> if (r.approvalId != null) pluralStringResource(R.plurals.asst_review_ready, r.total, n(r.total))
            else pluralStringResource(R.plurals.asst_review_nothing, r.total, n(r.total))
    }
    Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceContainerLow,
        modifier = Modifier.widthIn(max = 640.dp).fillMaxWidth().testTag("review-progress")) {
        Column(Modifier.padding(start = 12.dp, end = 4.dp, top = 4.dp, bottom = 8.dp)) {
            Row(Modifier.heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                if (r.live) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                else Icon(Icons.Outlined.FindInPage, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.width(10.dp))
                Text(head, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f)
                    .semantics { liveRegion = LiveRegionMode.Polite },
                    color = if (r.status == "failed") MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface)
                if (r.status == "running") TextButton(onClick = onStop, enabled = !acting, modifier = Modifier.testTag("review-stop")) {
                    Icon(Icons.Outlined.Pause, null, Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text(stringResource(R.string.asst_review_stop))
                }
                if (r.status == "stopped" || r.status == "failed") FilledTonalButton(onClick = onResume, enabled = !acting,
                    contentPadding = PaddingValues(horizontal = 14.dp), modifier = Modifier.testTag("review-continue")) {
                    Icon(Icons.Outlined.PlayArrow, null, Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text(stringResource(R.string.asst_review_continue))
                }
            }
            if (r.live || r.status == "stopped") {
                val pct = if (r.total > 0) (r.done.toFloat() / r.total).coerceIn(0f, 1f) else 1f
                LinearProgressIndicator(progress = { pct }, modifier = Modifier.fillMaxWidth().padding(end = 8.dp))
                Text(stringResource(R.string.asst_review_done_of, n(r.done), n(r.total)) + " · " +
                    pluralStringResource(R.plurals.asst_review_set_aside, r.setAside, n(r.setAside)),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 6.dp, end = 8.dp))
            }
        }
    }
}

/** The heading of a group of moves: "Move to Trash", "Archive", "Mark read", "Label “Receipts”". */
@Composable
private fun moveTitle(move: String, label: String?): String = when (move) {
    "trash" -> stringResource(R.string.asst_review_move_trash)
    "archive" -> stringResource(R.string.asst_review_move_archive)
    "read" -> stringResource(R.string.asst_review_move_read)
    else -> stringResource(R.string.asst_review_move_label, label.orEmpty())
}

/** A move's name outside a composable (the filter's name). */
private fun moveName(move: String, label: String?): String = when (move) {
    "trash" -> AppText.get(R.string.asst_review_move_trash)
    "archive" -> AppText.get(R.string.asst_review_move_archive)
    "read" -> AppText.get(R.string.asst_review_move_read)
    else -> AppText.get(R.string.asst_review_move_label, label.orEmpty())
}

/**
 * The filter for a sender's future mail that does what the review does to their mail now (the web's
 * filterFor in ReviewPlan.tsx): POST /rules `{name, query: "from:<email>", actions, enabled: true}`.
 * Null without the sender's address, or for a label move without its label's id.
 */
internal fun filterFor(g: ReviewGroup, s: ReviewSender): JsonObject? {
    val email = s.email?.takeIf { it.isNotBlank() } ?: return null
    val actions = when (g.move) {
        "trash" -> buildJsonObject { put("trash", true) }
        "archive" -> buildJsonObject { put("archive", true) }
        "read" -> buildJsonObject { put("mark_read", true) }
        "label" -> g.labelId?.let { id -> buildJsonObject { put("add_labels", JsonArray(listOf(JsonPrimitive(id)))) } }
        else -> null
    } ?: return null
    return buildJsonObject {
        put("name", "${s.from}: ${moveName(g.move, g.label)}")
        put("query", "from:$email")
        put("actions", actions)
        put("enabled", true)
    }
}

/** What identifies one sender's "Always …" on one review card. */
internal fun alwaysKey(reviewId: Long, g: ReviewGroup, s: ReviewSender): String = "$reviewId/${g.move}/${g.label.orEmpty()}/${s.from}"

/**
 * A review card's "Always …" in the chat (ACT agent.review_filter @android.assistant): the senders whose
 * filter was made ([made]) or is on its way ([busy]), by [alwaysKey], and the tap ([make]).
 */
class ReviewAlways(val made: Set<String>, val busy: Set<String>, val make: (Long, ReviewGroup, ReviewSender) -> Unit)

/** "Always move to Trash", "Always archive", "Always mark read", "Always label “Receipts”". */
@Composable
private fun alwaysTitle(move: String, label: String?): String = when (move) {
    "trash" -> stringResource(R.string.asst_review_always_trash)
    "archive" -> stringResource(R.string.asst_review_always_archive)
    "read" -> stringResource(R.string.asst_review_always_read)
    else -> stringResource(R.string.asst_review_always_label, label.orEmpty())
}

/** A sender's "Always …" under its row, or "Filter made" once it is made (the web's AlwaysButton). */
@Composable
private fun AlwaysButton(reviewId: Long, g: ReviewGroup, s: ReviewSender, always: ReviewAlways) {
    if (!s.suggestFilter || filterFor(g, s) == null) return
    val key = alwaysKey(reviewId, g, s)
    if (key in always.made) {
        Row(Modifier.padding(start = 36.dp).heightIn(min = 40.dp).testTag("review-filter-made"), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.Check, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.width(6.dp))
            Text(stringResource(R.string.asst_review_filter_made), style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        return
    }
    val tip = stringResource(R.string.asst_review_always_tip, s.email.orEmpty())
    val title = alwaysTitle(g.move, g.label)
    TextButton(onClick = { always.make(reviewId, g, s) }, enabled = key !in always.busy,
        contentPadding = PaddingValues(horizontal = 8.dp), modifier = Modifier.padding(start = 28.dp).testTag("review-always")
            .semantics { contentDescription = "$title. $tip" }) {
        Icon(Icons.Outlined.FilterAlt, null, Modifier.size(16.dp))
        Spacer(Modifier.width(6.dp))
        Text(title)
    }
}

// ---- the move menu (the web's ReviewPlan.tsx itemsOf, labelsOf, onMove)

/**
 * One conversation on a review card with the move the review proposed ([orig]): trash, archive, read,
 * label:<id>, keep, or unsure (the review's own "not sure", which the user can only leave).
 */
internal data class ReviewItem(
    val id: Long,
    val from: String,
    val subject: String,
    val date: String?,
    val orig: String,
    val reason: String = "",
    /** On a decided card: kept because the user chose Keep. */
    val byYou: Boolean = false,
)

/** A group's move as the menu and `changes` name it: trash, archive, read or label:<id>. */
internal fun moveKey(g: ReviewGroup): String = if (g.move == "label") "label:${g.labelId ?: ""}" else g.move

/** Every conversation the card shows, with the move the review proposed. */
internal fun reviewItems(review: ReviewPreview): List<ReviewItem> = buildList {
    review.groups.forEach { g ->
        g.senders.forEach { s ->
            // A card saved before the per-conversation list: its ids, with the sample subjects.
            val convs = s.conversations ?: s.conversationIds.mapIndexed { i, id -> ReviewConversation(id, s.subjects.getOrNull(i).orEmpty()) }
            convs.forEach { c -> add(ReviewItem(c.conversationId, s.from, c.subject, c.date, moveKey(g))) }
        }
    }
    review.kept.forEach { r -> add(ReviewItem(r.conversationId, r.from, r.subject, r.date, "keep", r.reason, r.byYou)) }
    review.unsure.forEach { r -> add(ReviewItem(r.conversationId, r.from, r.subject, r.date, "unsure", r.reason)) }
}

/** The labels the move menu offers: those the review could add, and any its groups use. */
internal fun reviewLabels(review: ReviewPreview): List<ReviewLabel> {
    val out = LinkedHashMap<Long, String>()
    review.labels.forEach { out[it.id] = it.name }
    review.groups.forEach { g -> if (g.move == "label" && g.labelId != null) out[g.labelId] = g.label.orEmpty() }
    return out.map { (id, name) -> ReviewLabel(id, name) }
}

/** The menu's moves, in order: Move to Trash, Archive, Mark read, each label, Keep. */
internal fun moveKeys(labels: List<ReviewLabel>): List<String> = listOf("trash", "archive", "read") + labels.map { "label:${it.id}" } + "keep"

/**
 * [changes] with the conversations [ids] sent to [move]. A move back to the review's own drops the change,
 * so the map holds only what differs from the review: its size is "N changed by you".
 */
internal fun withMove(items: List<ReviewItem>, changes: Map<Long, String>, ids: List<Long>, move: String): Map<Long, String> {
    val byId = items.associateBy { it.id }
    val next = changes.toMutableMap()
    ids.forEach { id -> if (byId[id]?.orig.let { it == null || it == move }) next.remove(id) else next[id] = move }
    return next
}

/**
 * The card as it stands with the user's [changes]: the moves in menu order, each with its senders (most
 * conversations first); what the review kept, what the user chose to keep, and what is still unsure.
 */
internal class ReviewLayout(
    val groups: List<Pair<String, List<Pair<String, List<ReviewItem>>>>>,
    val keptModel: List<ReviewItem>,
    val keptByYou: List<ReviewItem>,
    val unsure: List<ReviewItem>,
)

internal fun reviewLayout(items: List<ReviewItem>, labels: List<ReviewLabel>, changes: Map<Long, String>): ReviewLayout {
    fun current(i: ReviewItem) = changes[i.id] ?: i.orig
    val byMove = items.filter { current(it) != "keep" && current(it) != "unsure" }.groupBy(::current)
    val groups = moveKeys(labels).mapNotNull { k ->
        byMove[k]?.let { its -> k to its.groupBy { it.from }.toList().sortedWith(compareBy({ -it.second.size }, { it.first.lowercase() })) }
    }
    return ReviewLayout(groups,
        keptModel = items.filter { it.orig == "keep" && !it.byYou && current(it) == "keep" },
        keptByYou = items.filter { (it.orig != "keep" || it.byYou) && current(it) == "keep" },
        unsure = items.filter { current(it) == "unsure" })
}

/** A move's name: "Move to Trash", "Archive", "Mark read", "Label “Receipts”", "Keep", "Not sure". */
@Composable
private fun moveText(key: String, labels: List<ReviewLabel>): String = when {
    key == "keep" -> stringResource(R.string.asst_review_keep)
    key == "unsure" -> stringResource(R.string.asst_review_not_sure)
    key.startsWith("label:") -> stringResource(R.string.asst_review_move_label, labels.firstOrNull { "label:${it.id}" == key }?.name.orEmpty())
    else -> moveTitle(key, null)
}

/** "20 Oct": a conversation's date in the expanded sender. */
private fun day(d: String?): String = d?.let { catching { LocalDate.parse(it).format(LocaleFormats.date("MMMd", "MMM d")) }.getOrDefault(it) }.orEmpty()

/**
 * The move menu of a sender, a conversation or a kept or unsure row: a chip with the current move that
 * opens the moves (the web's MoveChip). [what] names the row for TalkBack ("What happens to Lumen Coffee").
 */
@Composable
private fun MoveMenu(value: String, labels: List<ReviewLabel>, enabled: Boolean, what: String, onChange: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    val name = moveText(value, labels)
    val desc = stringResource(R.string.asst_review_move_for, what)
    // Move to Trash in the error colour, as the web's chip.
    val tint = if (value == "trash") MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface
    Box {
        AssistChip(onClick = { open = true }, enabled = enabled,
            colors = AssistChipDefaults.assistChipColors(labelColor = tint, trailingIconContentColor = tint),
            label = { Text(name, maxLines = 1, overflow = TextOverflow.Ellipsis) },
            trailingIcon = { Icon(Icons.Outlined.ArrowDropDown, null, Modifier.size(AssistChipDefaults.IconSize)) },
            modifier = Modifier.heightIn(min = 48.dp).widthIn(max = 176.dp).testTag("review-move")
                .semantics { contentDescription = desc; stateDescription = name })
        DropdownMenu(open, { open = false }) {
            moveKeys(labels).forEach { k ->
                DropdownMenuItem({ Text(moveText(k, labels)) }, { open = false; if (k != value) onChange(k) },
                    leadingIcon = { if (k == value) Icon(Icons.Outlined.Check, null) else Spacer(Modifier.size(24.dp)) },
                    modifier = Modifier.testTag("review-move-$k"))
            }
        }
    }
}

/**
 * The body of a review card: a heading per move with its count, a row per sender with its count, up to
 * three sample subjects and its move menu (an expander opens its conversations, each with its own menu),
 * what was kept and why, and the kept and unsure conversations behind a fold each, with a menu per row.
 * Choosing a move puts the row under it. [changes]: the user's moves so far, by conversation id, only
 * those that differ from the review's. [always]: a sender the server suggests a filter for gets
 * "Always …" under its row while all its mail keeps the review's move; null shows none.
 * ACT agent.review_move @android.assistant @android.approvals
 */
@Composable
fun ReviewPlan(review: ReviewPreview, changes: Map<Long, String>, onChanges: (Map<Long, String>) -> Unit, editable: Boolean,
               always: ReviewAlways? = null) {
    val items = remember(review) { reviewItems(review) }
    val labels = remember(review) { reviewLabels(review) }
    val layout = reviewLayout(items, labels, changes)
    val onMove = { ids: List<Long>, m: String -> onChanges(withMove(items, changes, ids, m)) }
    val current = { i: ReviewItem -> changes[i.id] ?: i.orig }
    Column(Modifier.fillMaxWidth().testTag("review-plan"), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        layout.groups.forEach { (k, senders) ->
            // The review's own group for this move (none for a label only the user picked).
            val move = review.groups.firstOrNull { moveKey(it) == k }
            Column {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(moveText(k, labels), style = MaterialTheme.typography.titleSmall)
                    Spacer(Modifier.width(8.dp))
                    Text(n(senders.sumOf { it.second.size }), style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                senders.forEach { (from, mine) ->
                    key(k, from) {
                        SenderRow(review.reviewId, k, move, from, mine, items.filter { it.from == from && it.orig == k }, labels, onMove, editable, always)
                    }
                }
            }
        }
        val keptMore = (review.keptCount - review.kept.size).coerceAtLeast(0)
        val unsureMore = (review.unsureCount - review.unsure.size).coerceAtLeast(0)
        val keptModelCount = layout.keptModel.size + keptMore
        val aside = review.setAside.filterValues { it > 0 }.toList().sortedBy { (why, _) -> ASIDE.indexOf(why).let { if (it < 0) ASIDE.size else it } }
        if (aside.isNotEmpty() || keptModelCount > 0 || layout.keptByYou.isNotEmpty()) {
            val bits = aside.map { (why, c) -> asideText(why, c) } +
                (if (keptModelCount > 0) listOf(pluralStringResource(R.plurals.asst_review_aside_model, keptModelCount, n(keptModelCount))) else emptyList()) +
                (if (layout.keptByYou.isNotEmpty()) listOf(pluralStringResource(R.plurals.asst_review_aside_by_you, layout.keptByYou.size,
                    n(layout.keptByYou.size))) else emptyList())
            Text(stringResource(R.string.asst_review_kept_lead) + " " + bits.joinToString(", "), style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.testTag("review-kept"))
        }
        val unsureCount = layout.unsure.size + unsureMore
        if (layout.keptByYou.isNotEmpty() || keptModelCount > 0 || unsureCount > 0) Column {
            ReviewRows(pluralStringResource(R.plurals.asst_review_kept_by_you_rows, layout.keptByYou.size, n(layout.keptByYou.size)),
                layout.keptByYou, 0, "review-kept-by-you-rows", current, labels, onMove, editable, startOpen = true)
            ReviewRows(pluralStringResource(R.plurals.asst_review_kept_rows, keptModelCount, n(keptModelCount)), layout.keptModel,
                keptMore, "review-kept-rows", current, labels, onMove, editable)
            ReviewRows(pluralStringResource(R.plurals.asst_review_unsure_rows, unsureCount, n(unsureCount)), layout.unsure,
                unsureMore, "review-unsure-rows", current, labels, onMove, editable)
        }
    }
}

/**
 * A sender under one move: its name and count, its menu, and an expander when it has more than one
 * conversation (each then with its own menu); otherwise up to three sample subjects. [all]: this sender's
 * conversations the review proposed for this move, for "Always …".
 */
@Composable
private fun SenderRow(reviewId: Long, k: String, move: ReviewGroup?, from: String, items: List<ReviewItem>, all: List<ReviewItem>,
                     labels: List<ReviewLabel>, onMove: (List<Long>, String) -> Unit, editable: Boolean, always: ReviewAlways?) {
    var open by rememberSaveable { mutableStateOf(false) }
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("review-sender"), verticalAlignment = Alignment.CenterVertically) {
        if (items.size > 1) {
            val desc = stringResource(R.string.asst_review_conversations_from, from)
            IconButton(onClick = { open = !open }, modifier = Modifier.testTag("review-expand").semantics { contentDescription = desc }) {
                Icon(if (open) Icons.Outlined.ExpandMore else Icons.AutoMirrored.Outlined.KeyboardArrowRight, null, Modifier.size(20.dp))
            }
        } else Spacer(Modifier.width(48.dp))
        Column(Modifier.weight(1f).padding(vertical = 4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(from, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false))
                Spacer(Modifier.width(8.dp))
                Text(n(items.size), style = MaterialTheme.typography.bodyMedium, color = muted)
            }
            val samples = items.take(3).map { it.subject }.filter { it.isNotBlank() }
            if (!open && samples.isNotEmpty()) Text(samples.joinToString(" · "), style = MaterialTheme.typography.bodySmall,
                color = muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        Spacer(Modifier.width(8.dp))
        MoveMenu(k, labels, editable, from) { m -> onMove(items.map { it.id }, m) }
    }
    if (open) Column(Modifier.padding(start = 48.dp)) {
        items.forEach { c ->
            Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("review-conversation"), verticalAlignment = Alignment.CenterVertically) {
                Text(buildAnnotatedString {
                    append(c.subject)
                    if (!c.date.isNullOrBlank()) withStyle(SpanStyle(color = muted)) { append(" · " + day(c.date)) }
                }, style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                Spacer(Modifier.width(8.dp))
                MoveMenu(k, labels, editable, c.subject.ifBlank { from }) { m -> onMove(listOf(c.id), m) }
            }
        }
    }
    // "Always …" while the sender's mail is all still in the move the review proposed for it.
    val whole = all.size == items.size && items.all { it.orig == k }
    val s = move?.senders?.firstOrNull { it.from == from }
    if (always != null && whole && move != null && s != null) AlwaysButton(reviewId, move, s, always)
}

/**
 * "8 kept: still useful", "2 kept: you chose Keep" or "3 left alone: not sure", which opens to the
 * conversations, the model's reason and a move menu each; [more]: those the card does not list.
 */
@Composable
private fun ReviewRows(title: String, rows: List<ReviewItem>, more: Int, tag: String, current: (ReviewItem) -> String,
                       labels: List<ReviewLabel>, onMove: (List<Long>, String) -> Unit, editable: Boolean, startOpen: Boolean = false) {
    if (rows.isEmpty() && more <= 0) return
    var open by rememberSaveable { mutableStateOf(startOpen) }
    Column {
        TextButton(onClick = { open = !open }, contentPadding = PaddingValues(horizontal = 4.dp), modifier = Modifier.testTag(tag)) {
            Icon(if (open) Icons.Outlined.ExpandMore else Icons.AutoMirrored.Outlined.KeyboardArrowRight, null, Modifier.size(18.dp))
            Spacer(Modifier.width(6.dp))
            Text(title)
        }
        if (open) Column(Modifier.padding(start = 28.dp, bottom = 4.dp)) {
            val muted = MaterialTheme.colorScheme.onSurfaceVariant
            rows.forEach { r ->
                Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("review-row"), verticalAlignment = Alignment.CenterVertically) {
                    Text(buildAnnotatedString {
                        withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) { append(r.from) }
                        append("  ")
                        append(r.subject)
                        if (r.reason.isNotBlank()) withStyle(SpanStyle(color = muted)) { append(" · " + r.reason) }
                    }, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                    Spacer(Modifier.width(8.dp))
                    MoveMenu(current(r), labels, editable, "${r.from}: ${r.subject}") { m -> onMove(listOf(r.id), m) }
                }
            }
            if (more > 0) Text(pluralStringResource(R.plurals.asst_review_and_more, more, n(more)),
                style = MaterialTheme.typography.bodySmall, color = muted, modifier = Modifier.padding(vertical = 6.dp))
        }
    }
}
