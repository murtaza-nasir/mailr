package io.github.murtazanasir.mailr.ui.mail

import io.github.murtazanasir.mailr.look.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.settings.SwipeAction
import io.github.murtazanasir.mailr.ui.components.refuseObscuredTouches
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull

/**
 * Where a conversation sits, as far as its actions go. In Spam the way out is Not spam;
 * in Trash it is Restore, and Delete forever ends it. Neither place offers Report spam, and
 * Trash does not offer Delete (it would only trash the conversation again).
 */
enum class MailPlace {
    OTHER, SPAM, TRASH;

    companion object {
        /** The place a list view shows. */
        fun ofView(view: String): MailPlace = when (view) {
            "spam" -> SPAM
            "trash" -> TRASH
            else -> OTHER
        }

        /**
         * The reader's place: the list it was opened from when that is Spam or Trash, else the
         * conversation's own Trash or Spam label (a search or a notification may open it).
         */
        fun ofConversation(labelIds: List<Long>?, labels: List<Label>, listView: String): MailPlace {
            ofView(listView).let { if (it != OTHER) return it }
            val roles = labelIds.orEmpty().mapNotNull { id -> labels.firstOrNull { it.id == id }?.role }.toSet()
            return when {
                "trash" in roles -> TRASH
                "junk" in roles || "spam" in roles -> SPAM
                else -> OTHER
            }
        }
    }
}

/**
 * What sits where Archive sits, from the rows' own state (the
 * web's lib/rowArchive.ts). ARCHIVE: Archive changes something. INBOX: Move to Inbox in its
 * place. KEEP: Trash or Spam, whose actions stay as they are.
 */
enum class ArchiveSlot { ARCHIVE, INBOX, KEEP }

/**
 * Archive while it would change one of [rows]: a row in the Inbox, or a row that carries the
 * list's label ([contextLabelId]) when Archive also removes it ([removesCurrent]; the Later tab).
 * Otherwise Move to Inbox, also in an Inbox tab's label section that shows archived mail. Rows in
 * Trash or Spam keep their actions. No rows, or no labels yet: Archive.
 */
fun archiveSlot(rows: List<ConversationSummary>, place: MailPlace, labels: Map<Long, Label>,
                contextLabelId: Long?, removesCurrent: Boolean): ArchiveSlot {
    if (place != MailPlace.OTHER) return ArchiveSlot.KEEP
    // No rows, or the labels not loaded yet (no Inbox known): Archive, as before.
    if (rows.isEmpty() || labels.values.none { it.role == "inbox" }) return ArchiveSlot.ARCHIVE
    fun has(c: ConversationSummary, vararg roles: String) = c.labelIds.any { labels[it]?.role in roles }
    if (rows.all { has(it, "trash", "junk", "spam") }) return ArchiveSlot.KEEP
    val ctx = if (removesCurrent) contextLabelId else null
    return if (rows.any { has(it, "inbox") || (ctx != null && ctx in it.labelIds) }) ArchiveSlot.ARCHIVE else ArchiveSlot.INBOX
}

/** The setting archive_removes_current_label (on by default): Archive in a label's list also removes the label. */
fun archiveRemovesLabel(settings: JsonObject?): Boolean =
    (settings?.get("archive_removes_current_label") as? JsonPrimitive)?.booleanOrNull ?: true

/** The swipe's label in a place ("Restore", "Not spam", "Delete forever"); null keeps the usual one. */
fun swipeLabel(place: MailPlace, action: SwipeAction, slot: ArchiveSlot = ArchiveSlot.ARCHIVE): String? = when {
    slot == ArchiveSlot.INBOX && action == SwipeAction.ARCHIVE -> AppText.get(R.string.mail_move_to_inbox)
    place == MailPlace.TRASH && action == SwipeAction.ARCHIVE -> AppText.get(R.string.mail_restore)
    place == MailPlace.TRASH && action == SwipeAction.TRASH -> AppText.get(R.string.mail_delete_forever)
    place == MailPlace.SPAM && action == SwipeAction.ARCHIVE -> AppText.get(R.string.mail_not_spam)
    else -> null
}

/**
 * ACT mail.delete_forever @android.list: the confirmation. Its button ignores touches through an overlay,
 * like the other actions that cannot be undone.
 */
@Composable
fun DeleteForeverDialog(count: Int, onDismiss: () -> Unit, onConfirm: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.mail_delete_forever_title)) },
        text = { Text(pluralStringResource(R.plurals.mail_delete_forever_text, count, count)) },
        confirmButton = {
            TextButton(onClick = onConfirm, modifier = Modifier.refuseObscuredTouches().testTag("delete-forever-confirm")) {
                Text(stringResource(R.string.mail_delete_forever), color = MaterialTheme.colorScheme.error)
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.mail_cancel)) } },
    )
}
