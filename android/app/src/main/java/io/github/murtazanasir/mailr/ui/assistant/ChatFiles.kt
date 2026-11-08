package io.github.murtazanasir.mailr.ui.assistant

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.agent.ChatFile
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import io.github.murtazanasir.mailr.ui.util.Format
import io.github.murtazanasir.mailr.util.catching

/**
 * Files and images in the chat (ACT agent.chat_attach @android.assistant): the ones waiting above the
 * message box ([PendingFiles]) and the ones a sent message carries ([TurnFiles]). An image shows its
 * small copy (GET /agent/attachments/{id}/thumb, through [thumb]); a file shows its name and size.
 */
@Composable
internal fun PendingFiles(pending: List<PendingFile>, thumb: (suspend (String) -> ByteArray?)?, remove: ((String) -> Unit)?) {
    Row(Modifier.horizontalScroll(rememberScrollState()).padding(bottom = 6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        pending.forEach { p ->
            FileTile(p.name, p.size, p.file, thumb, uploading = p.file == null,
                remove = remove?.let { r -> { r(p.key) } }, modifier = Modifier.testTag("pending-attachment"))
        }
    }
}

/** The files and images of a sent message, over its bubble. */
@Composable
internal fun TurnFiles(files: List<ChatFile>, thumb: (suspend (String) -> ByteArray?)?) {
    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        files.forEach { f -> FileTile(f.name, f.size, f, thumb, uploading = false, remove = null, modifier = Modifier.testTag("turn-attachment")) }
    }
}

@Composable
private fun FileTile(name: String, size: Long, file: ChatFile?, thumb: (suspend (String) -> ByteArray?)?, uploading: Boolean,
                     remove: (() -> Unit)?, modifier: Modifier = Modifier) {
    val image = file?.takeIf { it.isImage && it.thumb && thumb != null }
    val bmp by produceState<ImageBitmap?>(null, image?.id) {
        val id = image?.id ?: return@produceState
        val bytes = thumb?.invoke(id) ?: return@produceState
        value = catching { BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap() }.getOrNull()
    }
    Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh, modifier = modifier) {
        Row(Modifier.padding(4.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(44.dp).clip(RoundedCornerShape(8.dp)), contentAlignment = Alignment.Center) {
                val shown = bmp
                when {
                    uploading -> CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                    shown != null -> Image(shown, stringResource(R.string.asst_attach_image, name), contentScale = ContentScale.Crop,
                        modifier = Modifier.size(44.dp))
                    else -> Icon(if (file?.isImage == true) Icons.Outlined.Image else Icons.Outlined.Description, null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Column(Modifier.padding(horizontal = 8.dp).widthIn(max = 160.dp)) {
                Text(name, style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(if (uploading) stringResource(R.string.asst_attach_uploading) else Format.bytes(size),
                    style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
            }
            if (remove != null) IconButton(onClick = remove, modifier = Modifier.size(32.dp).testTag("remove-attachment")) {
                Icon(Icons.Outlined.Close, stringResource(R.string.asst_attach_remove, name), Modifier.size(16.dp))
            }
        }
    }
}
