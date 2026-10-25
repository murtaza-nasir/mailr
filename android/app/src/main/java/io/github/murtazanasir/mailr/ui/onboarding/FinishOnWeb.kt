package io.github.murtazanasir.mailr.ui.onboarding

import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Language
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import io.github.murtazanasir.mailr.data.api.SetupState
import io.github.murtazanasir.mailr.ui.components.EmptyState

/**
 * A member who opens the app before finishing the web setup. The inbox says where to finish,
 * with the address, instead of an empty "Nothing here".
 */
object FinishOnWeb {
    /** Show the card: the server says no mail account is connected yet. */
    fun needed(setup: SetupState?): Boolean = setup != null && !setup.hasAccounts

    /** "mail.example.com/setup" for the text. */
    fun address(setup: SetupState): String = setup.url.removePrefix("https://").removePrefix("http://")

    fun body(setup: SetupState): String =
        AppText.get(R.string.onb_finish_web_body, address(setup))
}

@Composable
fun FinishOnWebState(setup: SetupState, open: () -> Unit) {
    EmptyState(Icons.Outlined.Language, stringResource(R.string.onb_finish_web_title), FinishOnWeb.body(setup),
        modifier = Modifier.testTag("finish-on-web"), action = stringResource(R.string.onb_finish_web_action) to open)
}
