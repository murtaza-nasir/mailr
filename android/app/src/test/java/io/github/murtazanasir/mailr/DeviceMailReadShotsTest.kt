package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.adaptive.layout.AnimatedPane
import androidx.compose.material3.adaptive.layout.ListDetailPaneScaffold
import androidx.compose.material3.adaptive.layout.ListDetailPaneScaffoldRole
import androidx.compose.material3.adaptive.navigation.rememberListDetailPaneScaffoldNavigator
import androidx.compose.runtime.LaunchedEffect
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.data.api.Identity
import io.github.murtazanasir.mailr.ui.compose.ComposeCallbacks
import io.github.murtazanasir.mailr.ui.compose.ComposeScreen
import io.github.murtazanasir.mailr.ui.compose.ComposeState
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** Reading and writing: the conversation, a citation's quote banner, list and detail together, and compose. On every device of [DeviceShots], light and dark. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class DeviceMailReadShotsTest(device: String, qualifiers: String, dark: Boolean) : DeviceShots(device, qualifiers, dark) {
    @Test fun conversation() = shot("conversation") { Conversation() }

    /** Opened from a citation: the banner with the quote, in the colours of the web's quote mark. Phone only. */
    @Test fun conversationQuote() {
        org.junit.Assume.assumeTrue(device == "phone")
        shot("conversation-quote") { Conversation(quote = "Award nominations close Sep 30.") }
    }

    /** Two panes on the Fold and the tablet. On the phone one pane shows the conversation, the same image as [conversation]. */
    @Test fun listDetail() {
        org.junit.Assume.assumeTrue(device != "phone")
        shot("list-detail") {
            val nav = rememberListDetailPaneScaffoldNavigator<String>()
            LaunchedEffect(Unit) { nav.navigateTo(ListDetailPaneScaffoldRole.Detail, "3066") }
            ListDetailPaneScaffold(nav.scaffoldDirective, nav.scaffoldValue,
                listPane = { AnimatedPane { List() } },
                detailPane = { AnimatedPane { Conversation() } })
        }
    }

    @Test fun compose() = shot("compose") {
        ComposeScreen(
            ComposeState(loading = false, identities = listOf(Identity(1, "me@example.com", "Alex Morgan", isDefault = true)),
                identityId = 1, to = listOf(Address("alice@example.com", "Alice Chen")), subject = "Re: Quarterly plan",
                blocks = io.github.murtazanasir.mailr.ui.compose.ComposeBody.ofText("Friday at 10 works for me.\n\nThanks,\nRowan"), quoted = "<div class=\"mailr_quote\">On Mon, Alice wrote: …</div>",
                title = "Reply", saveStatus = "Saved"),
            SnackbarHostState(),
            ComposeCallbacks({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> true }, { _, _ -> }, { _, _ -> }, { _, _ -> }, {}, {}, {}),
        )
    }
}
