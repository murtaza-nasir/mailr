package io.github.murtazanasir.mailr.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.StickyNote2
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.Mail
import androidx.compose.material.icons.outlined.SmartToy
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Text
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteScaffoldDefaults
import androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteType
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.look.WebTab
import io.github.murtazanasir.mailr.look.WebTabBar

/**
 * [AppNavigation] in the web app's look (Settings → Look → Style: Web style): the web's tab bar on phones
 * (io.github.murtazanasir.mailr.look.WebTabBar), a flat rail on wider screens. The content keeps one place in
 * the composition whichever bar shows, so opening the keyboard (which hides the bar) loses no state.
 * The web's tab bar draws the same outlined pictures for the current tab and the others.
 */
@Composable
internal fun WebAppNavigation(tab: Tab, setTab: (Tab) -> Unit, inboxUnread: Int, assistantBadge: Int, assistantName: String,
                              content: @Composable () -> Unit) {
    val imeVisible = WindowInsets.ime.getBottom(LocalDensity.current) > 0
    val type = NavigationSuiteScaffoldDefaults.calculateFromAdaptiveInfo(currentWindowAdaptiveInfo())
    val bar = type == NavigationSuiteType.NavigationBar
    @Composable fun icon(t: Tab) {
        val icon = when (t) {
            Tab.MAIL -> Icons.Outlined.Mail
            Tab.ASSISTANT -> Icons.Outlined.SmartToy
            Tab.CALENDAR -> Icons.Outlined.CalendarMonth
            Tab.TASKS -> Icons.Outlined.TaskAlt
            Tab.NOTES -> Icons.AutoMirrored.Outlined.StickyNote2
        }
        if (t == Tab.MAIL && inboxUnread > 0) {
            androidx.compose.material3.BadgedBox(badge = {
                androidx.compose.material3.Badge { Text(if (inboxUnread > 99) stringResource(R.string.ui_shell_badge_capped) else "$inboxUnread") }
            }) { Icon(icon, null) }
        } else if (t == Tab.ASSISTANT && assistantBadge > 0) {
            io.github.murtazanasir.mailr.ui.approvals.ApprovalsBadged(assistantBadge) { Icon(icon, null) }
        } else Icon(icon, null)
    }
    @Composable fun label(t: Tab) = Text(stringResource(t.label, assistantName), maxLines = 1,
        overflow = TextOverflow.Ellipsis)
    Row(Modifier.fillMaxSize()) {
        if (!bar && type != NavigationSuiteType.None) {
            NavigationRail(containerColor = MaterialTheme.colorScheme.background) {
                Tab.entries.forEach { t ->
                    NavigationRailItem(selected = t == tab, onClick = { setTab(t) }, icon = { icon(t) }, label = { label(t) },
                        modifier = io.github.murtazanasir.mailr.ui.approvals.waitingSemantics(if (t == Tab.ASSISTANT) assistantBadge else 0))
                }
            }
        }
        Column(Modifier.weight(1f).fillMaxHeight()) {
            val inset = if (bar && !imeVisible) Modifier else Modifier.windowInsetsPadding(WindowInsets.navigationBars.only(WindowInsetsSides.Bottom))
            Box(Modifier.weight(1f).fillMaxWidth().clipToBounds().then(inset)) { content() }
            if (bar && !imeVisible) {
                WebTabBar(Tab.entries.map { t ->
                    WebTab(selected = t == tab, onClick = { setTab(t) },
                        modifier = io.github.murtazanasir.mailr.ui.approvals.waitingSemantics(if (t == Tab.ASSISTANT) assistantBadge else 0),
                        icon = { icon(t) }, label = { label(t) })
                })
            }
        }
    }
}
