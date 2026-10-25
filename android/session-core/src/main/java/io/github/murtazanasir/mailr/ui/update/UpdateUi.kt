@file:OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)

package io.github.murtazanasir.mailr.ui.update

import io.github.murtazanasir.mailr.session.R
import androidx.compose.ui.res.stringResource
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.SystemUpdate
import androidx.compose.material3.Icon
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.update.AppUpdates
import io.github.murtazanasir.mailr.data.update.AvailableUpdate

/**
 * The app's screens with the update banner above them while the server refuses this version
 * ([AppUpdates.required]). The banner takes the status bar's space, so the screens below it do not
 * pad for the status bar again.
 */
@Composable
fun UpdateRequiredFrame(updates: AppUpdates, content: @Composable () -> Unit) {
    val required by updates.required.collectAsState()
    if (!required) {
        content()
        return
    }
    val ctx = LocalContext.current
    Column(Modifier.fillMaxSize()) {
        UpdateRequiredBanner(updates.displayName, onUpdate = { updates.open(ctx) }, Modifier.statusBarsPadding())
        Box(Modifier.weight(1f).fillMaxWidth().consumeWindowInsets(WindowInsets.statusBars)) { content() }
    }
}

/** "Update mailr to keep syncing", full width, with Update. Changes made meanwhile wait on the phone. */
@Composable
fun UpdateRequiredBanner(appName: String, onUpdate: () -> Unit, modifier: Modifier = Modifier) {
    val (bg, fg) = io.github.murtazanasir.mailr.look.bannerColors(io.github.murtazanasir.mailr.look.BannerTone.Danger,
        MaterialTheme.colorScheme.errorContainer, MaterialTheme.colorScheme.onErrorContainer)
    Surface(color = bg, contentColor = fg,
        modifier = Modifier.fillMaxWidth().testTag("update-required")) {
        Row(modifier.fillMaxWidth().heightIn(min = 56.dp).padding(start = 16.dp, end = 8.dp, top = 6.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.SystemUpdate, null)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(stringResource(R.string.session_update_required_title, appName), style = MaterialTheme.typography.titleSmall)
                Text(stringResource(R.string.session_update_required_detail), style = MaterialTheme.typography.bodySmall)
            }
            TextButton(onClick = onUpdate, modifier = Modifier.heightIn(min = 48.dp).testTag("update-required-action")) {
                Text(stringResource(R.string.session_update), color = fg)
            }
        }
    }
}

/** The quiet Settings row for a newer version on the server; nothing when there is none. */
@Composable
fun UpdateAvailableRow(appName: String, update: AvailableUpdate?, onUpdate: () -> Unit, modifier: Modifier = Modifier) {
    if (update == null) return
    ListItem(
        headlineContent = { Text(stringResource(R.string.session_update_available)) },
        supportingContent = { Text(if (update.versionName.isBlank()) stringResource(R.string.session_update_ready_unnamed, appName)
            else stringResource(R.string.session_update_ready, appName, update.versionName)) },
        leadingContent = { Icon(Icons.Outlined.SystemUpdate, null) },
        trailingContent = { TextButton(onClick = onUpdate) { Text(stringResource(R.string.session_update)) } },
        modifier = modifier.fillMaxWidth().clickable(onClick = onUpdate).testTag("update-available"),
    )
}
