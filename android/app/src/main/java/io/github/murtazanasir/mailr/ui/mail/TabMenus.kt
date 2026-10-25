package io.github.murtazanasir.mailr.ui.mail

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import io.github.murtazanasir.mailr.look.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.disabled
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.ShowInTabChoices
import io.github.murtazanasir.mailr.data.api.ShowInTabTarget
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/**
 * "Show in tab" and "Category", as on the web:
 * Show in tab lists exactly the active layout's tabs, in tab-bar order, with one mark on the tab
 * the conversations show in now (the server works it out with the lists' own rules); a tab defined
 * by a search or a state filter is greyed out with the reason under its name. Category is the raw
 * category, under its own item.
 */
object TabMenus {
    /** The categories in menu order, with their names. */
    val CATEGORIES: List<Pair<String, String>> get() = listOf("primary" to R.string.mail_category_primary, "promotions" to R.string.mail_category_promotions, "social" to R.string.mail_category_social, "updates" to R.string.mail_category_updates, "forums" to R.string.mail_category_forums, "purchases" to R.string.mail_category_purchases)
        .map { (id, name) -> id to AppText.get(name) }

    fun categoryName(id: String): String = CATEGORIES.firstOrNull { it.first == id }?.second ?: id

    /** The category every conversation has (no category reads as Primary), or null when they differ. */
    fun sharedCategory(categories: List<String?>): String? {
        if (categories.isEmpty()) return null
        val set = categories.map { it ?: "primary" }.toSet()
        return set.singleOrNull()
    }

    /** "Show in tab" is offered when the layout has more than one tab. */
    fun offersShowInTab(tabCount: Int): Boolean = tabCount > 1

    fun shownIn(name: String, n: Int): String =
        if (n == 1) AppText.get(R.string.mail_shown_in_one, name) else AppText.plural(R.plurals.mail_shown_in_n, n, n, name)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ShowInTabSheet(choices: ShowInTabChoices?, onDismiss: () -> Unit, onPick: (ShowInTabTarget) -> Unit) {
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.navigationBarsPadding().padding(bottom = 8.dp)) {
            Text(stringResource(R.string.mail_show_in_tab), style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(horizontal = 24.dp))
            if (choices == null) CircularProgressIndicator(Modifier.padding(24.dp))
            else ShowInTabList(choices, onPick)
        }
    }
}

/** The sheet's rows (tested on their own). */
@Composable
fun ShowInTabList(choices: ShowInTabChoices, onPick: (ShowInTabTarget) -> Unit) {
    Column(Modifier.fillMaxWidth()) {
        for (t in choices.tabs) {
            val here = choices.current == t.id
            val dim = if (t.disabled) Modifier.semantics { disabled() } else Modifier
            ListItem(
                headlineContent = { Text(t.name, color = if (t.disabled) MaterialTheme.colorScheme.onSurface.copy(alpha = 0.4f) else Color.Unspecified) },
                supportingContent = t.reason?.let { r -> { Text(r, style = MaterialTheme.typography.bodySmall) } },
                trailingContent = { RadioButton(here, onClick = null, enabled = !t.disabled) },
                colors = ListItemDefaults.colors(containerColor = Color.Transparent),
                modifier = Modifier
                    .semantics { selected = here }
                    .then(dim)
                    .clickable(enabled = !t.disabled, role = Role.RadioButton) { if (!here) onPick(t) },
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CategorySheet(current: String?, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.navigationBarsPadding().padding(bottom = 8.dp)) {
            Text(stringResource(R.string.mail_category), style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(horizontal = 24.dp))
            CategoryList(current, onPick)
        }
    }
}

@Composable
fun CategoryList(current: String?, onPick: (String) -> Unit) {
    Column(Modifier.fillMaxWidth()) {
        for ((id, name) in TabMenus.CATEGORIES) {
            val here = current == id
            ListItem(
                headlineContent = { Text(name) },
                trailingContent = { RadioButton(here, onClick = null) },
                colors = ListItemDefaults.colors(containerColor = Color.Transparent),
                modifier = Modifier.semantics { selected = here }.clickable(role = Role.RadioButton) { if (!here) onPick(id) },
            )
        }
    }
}
