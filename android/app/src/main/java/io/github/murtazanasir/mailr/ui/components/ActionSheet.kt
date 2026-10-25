package io.github.murtazanasir.mailr.ui.components

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

/** One action in an [ActionSheet]: a label, an icon, and what a tap does (the sheet closes first). */
class SheetAction(val label: String, val icon: ImageVector, val tag: String? = null, val onClick: () -> Unit)

/** A group of actions, with an optional heading ("Create from email", "Organize", "More"). */
class SheetGroup(val title: String?, val actions: List<SheetAction>)

/**
 * A ⋮ menu as a bottom sheet, so a long menu never runs off the
 * screen. The most-used actions come first; the rest sit in headed groups,
 * divided by lines. Every row has an icon and is 48 dp tall. The sheet scrolls when it is taller
 * than the screen, so no action is ever off screen. Empty groups are left out.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ActionSheet(title: String?, groups: List<SheetGroup>, onDismiss: () -> Unit, modifier: Modifier = Modifier) {
    val state = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = state, modifier = modifier.testTag("action-sheet")) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).navigationBarsPadding().padding(bottom = 8.dp)) {
            title?.let {
                Text(it, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(start = 24.dp, end = 24.dp, bottom = 8.dp).semantics { heading() })
            }
            groups.filter { it.actions.isNotEmpty() }.forEachIndexed { i, g ->
                if (i > 0 || title != null) HorizontalDivider(Modifier.padding(vertical = 4.dp))
                g.title?.let {
                    Text(it, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(start = 24.dp, end = 24.dp, top = 8.dp, bottom = 4.dp).semantics { heading() })
                }
                g.actions.forEach { a ->
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.Start,
                        modifier = Modifier.fillMaxWidth().then(if (a.tag != null) Modifier.testTag(a.tag) else Modifier)
                            .heightIn(min = 48.dp)
                            .clickable(role = Role.Button) { onDismiss(); a.onClick() }
                            .padding(horizontal = 24.dp, vertical = 12.dp)) {
                        Icon(a.icon, null, Modifier.size(24.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.width(20.dp))
                        Text(a.label, style = MaterialTheme.typography.bodyLarge)
                    }
                }
            }
        }
    }
}
