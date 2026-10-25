package io.github.murtazanasir.mailr.look

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

/**
 * A chip with its own × (remove, hide) beside the chip's own tap. The × is drawn at 18 dp inside the
 * chip, as before; its touch target is a 48 dp square over the chip's end (WCAG 2.5.8 and Android's
 * 48 dp guideline), laid over the chip so the chip keeps its size. The chip's own tap keeps the rest.
 */
@Composable
fun ChipWithClose(
    closeDescription: String,
    onClose: () -> Unit,
    modifier: Modifier = Modifier,
    chip: @Composable (closeIcon: @Composable () -> Unit) -> Unit,
) {
    Box(modifier) {
        chip { Icon(Icons.Filled.Close, null, Modifier.size(18.dp)) }
        Box(
            Modifier.align(Alignment.CenterEnd).size(CHIP_CLOSE_TARGET)
                .clickable(role = Role.Button, onClick = onClose)
                .semantics { contentDescription = closeDescription },
        )
    }
}

/** The × target's side. */
val CHIP_CLOSE_TARGET = 48.dp
