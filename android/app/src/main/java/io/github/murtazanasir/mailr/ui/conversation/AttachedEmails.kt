package io.github.murtazanasir.mailr.ui.conversation

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
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
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material.icons.outlined.Mail
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.AttachedEmailBody
import io.github.murtazanasir.mailr.data.api.AttachedEmailSummary
import io.github.murtazanasir.mailr.data.api.Attachment
import io.github.murtazanasir.mailr.data.api.Message
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.look.AssistChip
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.ui.util.Format
import io.github.murtazanasir.mailr.data.api.SafeText
import io.github.murtazanasir.mailr.ui.components.Shimmer

// ACT mail.open_attached_email @android.reader
// Emails attached to a message (message/rfc822: a forward as attachment, a digest, a bounce's
// original), one card each, as on the web. Closed: sender,
// subject, date and first words. Open: its own body, sanitised by the server like any body, with
// the same image rule, and its files. A message with no text of its own opens its first card.

sealed interface AttachedState {
    data object Loading : AttachedState
    data class Ready(val body: AttachedEmailBody, val imagesAllowed: Boolean) : AttachedState
    data class Failed(val message: String) : AttachedState
}

fun attachedKey(messageId: Long, part: Int) = "$messageId:$part"

/** The attachments of a message that are attached emails with a summary (cards, not file chips). */
fun Message.attachedEmails(): List<Attachment> = attachments.filter { it.email != null }

/** "Attached email: <subject> — <sender>". */
fun attachedEmailLabel(e: AttachedEmailSummary): String = AppText.get(
    R.string.conv_attached_email_label,
    SafeText.isolate(e.subject.ifBlank { AppText.get(R.string.conv_attached_email_no_subject) }),
    SafeText.isolate(e.fromName.ifBlank { e.fromEmail }.ifBlank { AppText.get(R.string.conv_attached_email_unknown_sender) }),
)

@Composable
internal fun AttachedEmailCards(m: Message, states: Map<String, AttachedState>, actions: ConversationActions) {
    val list = m.attachedEmails()
    if (list.isEmpty()) return
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        list.forEachIndexed { i, a ->
            AttachedEmailCard(m, a, states[attachedKey(m.id, a.part)], actions, startOpen = i == 0 && m.snippet.isBlank())
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AttachedEmailCard(m: Message, a: Attachment, state: AttachedState?, actions: ConversationActions, startOpen: Boolean) {
    val e = a.email ?: return
    var open by rememberSaveable(m.id, a.part) { mutableStateOf(startOpen) }
    if (open) LaunchedEffect(m.id, a.part) { actions.openAttachedEmail(m, a.part, null) }
    val cs = MaterialTheme.colorScheme
    val label = attachedEmailLabel(e)
    val stateText = stringResource(if (open) R.string.conv_attached_email_open else R.string.conv_attached_email_closed)
    Surface(shape = RoundedCornerShape(12.dp), border = BorderStroke(1.dp, cs.outlineVariant), color = cs.surface,
        modifier = Modifier.fillMaxWidth().testTag("attached-email")) {
        Column {
            Row(
                Modifier.fillMaxWidth().heightIn(min = 48.dp)
                    .clickable(role = Role.Button, onClickLabel = stringResource(if (open) R.string.conv_attached_email_hide else R.string.conv_attached_email_show)) { open = !open }
                    .semantics { stateDescription = stateText }
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(Icons.Outlined.Mail, null, Modifier.size(18.dp), tint = cs.onSurfaceVariant)
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(label, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium, maxLines = 2,
                        overflow = TextOverflow.Ellipsis)
                    e.date?.let { Text(Format.messageDate(it), style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant) }
                    if (!open && e.excerpt.isNotBlank()) {
                        Text(e.excerpt, style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant, maxLines = 2,
                            overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 2.dp))
                    }
                }
                Icon(if (open) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore, null, tint = cs.onSurfaceVariant)
            }
            if (open) {
                HorizontalDivider(color = cs.outlineVariant)
                AttachedEmailBodyView(m, a, state, actions)
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AttachedEmailBodyView(m: Message, a: Attachment, state: AttachedState?, actions: ConversationActions) {
    val cs = MaterialTheme.colorScheme
    when (state) {
        null, AttachedState.Loading -> Column(Modifier.padding(12.dp)) {
            Shimmer(Modifier.fillMaxWidth().heightIn(min = 12.dp)); Spacer(Modifier.size(8.dp))
            Shimmer(Modifier.fillMaxWidth(0.7f).heightIn(min = 12.dp))
        }
        is AttachedState.Failed -> Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(stringResource(R.string.conv_attached_email_failed, state.message), style = MaterialTheme.typography.bodySmall,
                color = cs.error, modifier = Modifier.weight(1f))
            TextButton(onClick = { actions.openAttachedEmail(m, a.part, false) }) { Text(stringResource(R.string.conv_retry)) }
        }
        is AttachedState.Ready -> Column(Modifier.padding(vertical = 8.dp)) {
            val b = state.body
            val style = MaterialTheme.typography.bodySmall
            Column(Modifier.padding(horizontal = 12.dp)) {
                Text(stringResource(R.string.conv_detail_from, SafeText.isolate(b.from.display), SafeText.isolate(b.from.email)),
                    style = style, color = cs.onSurfaceVariant)
                if (b.to.isNotEmpty()) Text(stringResource(R.string.conv_detail_to, b.to.joinToString { it.email }), style = style,
                    color = cs.onSurfaceVariant)
            }
            // The same image rule as a body: blocked until asked; Spam was answered blocked above.
            if (b.remoteImages && !state.imagesAllowed) {
                Surface(color = cs.surfaceContainerHigh, shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 4.dp)) {
                    Row(Modifier.padding(start = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.Image, null, Modifier.size(18.dp)); Spacer(Modifier.width(8.dp))
                        Text(stringResource(if (b.junk) R.string.conv_images_hidden_spam else R.string.conv_images_hidden), style = style,
                            modifier = Modifier.weight(1f))
                        if (!b.junk) TextButton(onClick = { actions.openAttachedEmail(m, a.part, true) }) { Text(stringResource(R.string.conv_show_images)) }
                    }
                }
            }
            val dark = cs.surface.luminanceIsDark()
            val paper = dark && b.source == "html"
            Surface(color = if (paper) Color(0xFFF8F9FA) else Color.Transparent, shape = RoundedCornerShape(10.dp),
                modifier = Modifier.fillMaxWidth().padding(horizontal = if (paper) 8.dp else 12.dp, vertical = 4.dp)) {
                MessageWebView(b.html ?: Format.textToHtml(b.text.orEmpty()), showQuote = false, darkText = !dark || paper,
                    allowImages = state.imagesAllowed, onMailto = actions.mailto, modifier = Modifier.padding(if (paper) 10.dp else 0.dp))
            }
            val files = b.attachments.filter { !it.inline }
            if (files.isNotEmpty()) FlowRow(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                files.forEach { f ->
                    val part = f.part
                    AssistChip(
                        // A file of the attached email opens from the outer message, like any attachment.
                        onClick = { if (part != null) actions.openAttachment(m, Attachment(part, f.filename, f.type, f.size)) },
                        enabled = part != null,
                        label = {
                            Column(Modifier.widthIn(max = 180.dp)) {
                                Text(SafeText.isolate(f.filename), maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text(Format.bytes(f.size), style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant)
                            }
                        },
                        leadingIcon = { Icon(if (f.email != null) Icons.Outlined.Mail else Icons.Outlined.Description, null) },
                    )
                }
            }
        }
    }
}
