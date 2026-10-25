package io.github.murtazanasir.mailr.ui.conversation

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.SmartToy
import io.github.murtazanasir.mailr.look.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.AssistantRun
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.ui.util.Format
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R

/**
 * ACT brief.read @android.reader on android.reader: the brief a standing order or routine wrote on this
 * conversation, above the messages (as on the web). Citation numbers open the email they quote;
 * images in the text never load. Collapsing is remembered per brief while the screen lives.
 */
/** Links in a brief: "mailr-cite:n" opens the cited email, anything else goes to the browser. */
internal fun briefLinks(brief: AssistantRun, openCitation: (Citation) -> Unit, openUrl: (String) -> Unit) = object : UriHandler {
    override fun openUri(uri: String) {
        if (uri.startsWith("mailr-cite:")) {
            uri.removePrefix("mailr-cite:").toIntOrNull()?.let { n -> brief.citations.firstOrNull { it.n == n }?.let(openCitation) }
        } else openUrl(uri)
    }
}

@Composable
fun BriefCard(brief: AssistantRun, assistantName: String, openCitation: (Citation) -> Unit, openUrl: (String) -> Unit) {
    var open by rememberSaveable(brief.id) { mutableStateOf(true) }
    val handler = remember(brief.citations) { briefLinks(brief, openCitation, openUrl) }
    Card(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh)) {
        Column(Modifier.padding(16.dp).animateContentSize()) {
            Row(verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.fillMaxWidth().clickable(onClickLabel = if (open) stringResource(R.string.conv_collapse_brief) else stringResource(R.string.conv_expand_brief)) { open = !open }) {
                Icon(Icons.Outlined.SmartToy, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(8.dp))
                Column(Modifier.weight(1f)) {
                    Text(assistantName, style = MaterialTheme.typography.titleSmall)
                    val order = if (brief.orderBuiltin == "forwarded_notes") stringResource(R.string.orders_builtin_name) else brief.orderName
                    val sub = listOfNotNull(order, Format.parse(brief.finishedAt)?.let { Format.relative(it) }).joinToString(" · ")
                    if (sub.isNotEmpty()) Text(sub, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                Icon(if (open) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore, null)
            }
            if (open) {
                Spacer(Modifier.size(8.dp))
                CompositionLocalProvider(LocalUriHandler provides handler) {
                    // The same Markdown renderer as the chat: modest headings, tables for the width (ui/present).
                    io.github.murtazanasir.mailr.ui.present.AgentMarkdown(brief.output)
                }
            }
        }
    }
}
