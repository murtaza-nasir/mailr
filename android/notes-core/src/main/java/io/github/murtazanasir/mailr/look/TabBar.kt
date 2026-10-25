package io.github.murtazanasir.mailr.look

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ProvideTextStyle
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp

/** One tab of [WebTabBar]: its picture (badge included) and its name. */
@Immutable
class WebTab(
    val selected: Boolean,
    val onClick: () -> Unit,
    val modifier: Modifier = Modifier,
    val icon: @Composable () -> Unit,
    val label: @Composable () -> Unit,
)

/**
 * The web app's tab bar on a phone (phone.css .phone-tabs): 64 dp on the surface container with a
 * 1 px top border; each tab a 56 × 32 indicator over a 12 sp name, the current one on the navigation
 * colour. Tabs keep Material's semantics (a selectable group of Role.Tab) and full-height targets.
 */
@Composable
fun WebTabBar(tabs: List<WebTab>, modifier: Modifier = Modifier) {
    val p = look.palette
    val cs = MaterialTheme.colorScheme
    Row(
        modifier.fillMaxWidth()
            .background(cs.surfaceContainer)
            .then(Hairline.top(p.divider))
            .windowInsetsPadding(WindowInsets.navigationBars)
            .height(WebTokens.TabbarH)
            .selectableGroup(),
        horizontalArrangement = Arrangement.SpaceEvenly,
    ) {
        tabs.forEach { t ->
            Column(
                Modifier.weight(1f).fillMaxHeight().then(t.modifier)
                    .selectable(t.selected, onClick = t.onClick, role = Role.Tab, interactionSource = null, indication = ripple(bounded = false, radius = 32.dp))
                    .padding(top = 6.dp, bottom = 4.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                Box(Modifier.size(56.dp, 32.dp).clip(CircleShape).background(if (t.selected) cs.secondaryContainer else Color.Transparent),
                    contentAlignment = Alignment.Center) {
                    CompositionLocalProvider(LocalContentColor provides if (t.selected) cs.onSecondaryContainer else p.text2) {
                        Box(Modifier.size(24.dp), contentAlignment = Alignment.Center) { t.icon() }
                    }
                }
                Spacer(Modifier.height(4.dp))
                CompositionLocalProvider(LocalContentColor provides if (t.selected) p.text else p.text2) {
                    ProvideTextStyle(WebTheme.style(WebTokens.TabLabel).copy(textAlign = androidx.compose.ui.text.style.TextAlign.Center)) {
                        Box(Modifier.padding(horizontal = 2.dp)) { t.label() }
                    }
                }
            }
        }
    }
}

