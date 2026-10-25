// ACT-SURFACES: android.settings
package io.github.murtazanasir.mailr.ui.settings

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTextInput
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.api.TrustedAddress
import io.github.murtazanasir.mailr.data.api.TrustedLastMessage
import io.github.murtazanasir.mailr.data.api.TrustedListing
import io.github.murtazanasir.mailr.data.api.TrustedSuggestion
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.until
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.concurrent.CopyOnWriteArrayList

/** The addresses in memory (example domains only); records every call. */
class FakeTrustedStore : TrustedStore {
    val calls = CopyOnWriteArrayList<String>()
    var listing = TrustedListing(
        addresses = listOf(
            TrustedAddress(1, "alex@work.example", "confirmed", lastMessage = TrustedLastMessage("Fw: Ops Review", null, true)),
            TrustedAddress(2, "alex@school.example", "pending"),
        ),
        suggestions = listOf(TrustedSuggestion("alex.rivera@lab.example", "Alex Rivera", 4)),
    )

    override suspend fun list(): TrustedListing { calls += "list"; return listing }
    override suspend fun add(email: String): TrustedAddress { calls += "add:$email"; return TrustedAddress(3, email) }
    override suspend fun confirm(id: Long, code: String): TrustedAddress { calls += "confirm:$id:$code"; return TrustedAddress(id, "", "confirmed") }
    override suspend fun resend(id: Long): TrustedAddress { calls += "resend:$id"; return TrustedAddress(id) }
    override suspend fun remove(id: Long) { calls += "remove:$id" }
    override suspend fun setElsewhere(addresses: List<String>) {
        calls += "elsewhere:${addresses.joinToString(",")}"
        listing = listing.copy(handledElsewhere = addresses)
    }
}

/**
 * Settings → Ambrose → Trusted addresses on a phone (Robolectric): trusted.list (the addresses,
 * their state, the last mail's verification, suggestions), trusted.confirm with a code, trusted.resend,
 * trusted.add (typed and from a suggestion), trusted.remove after the dialog, the addresses another
 * assistant handles (add, remove), and the way to Standing orders (the identity page has no mode now).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class TrustedScreenTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private var opened = 0

    private fun show(store: FakeTrustedStore = FakeTrustedStore()): FakeTrustedStore {
        compose.setContent { MaterialTheme { TrustedPage(store, "Ambrose", onBack = {}, openOrders = { opened++ }) } }
        compose.until("\"list\" in store.calls") { "list" in store.calls }
        compose.waitForIdle()
        return store
    }

    private fun scrollTo(tag: String) = compose.onNodeWithTag("trusted-list").performScrollToNode(hasTestTag(tag))

    @Test fun listsAddressesWithTheirState() {
        show()
        compose.onAllNodesWithTag("trusted-address").assertCountEquals(2)
        compose.onNodeWithText("alex@work.example").assertExists()
        compose.onNodeWithText("Confirmed\nIts last mail was verified by its domain.").assertExists()
        compose.onNodeWithText("Code sent").assertExists()
        // Only the address that waits has a code field.
        compose.onAllNodesWithTag("trusted-code").assertCountEquals(1)
    }

    @Test fun confirmResendAddAndRemove() {
        val store = show()
        compose.onNodeWithTag("trusted-code").performTextInput("123456")
        compose.onNodeWithTag("trusted-confirm").performClick()
        compose.until("store.calls.any { it.startsWith(\"confirm:\") }") { store.calls.any { it.startsWith("confirm:") } }
        assertTrue("confirm:2:123456" in store.calls)
        compose.onNodeWithTag("trusted-resend").performClick()
        compose.until("\"resend:2\" in store.calls") { "resend:2" in store.calls }
        scrollTo("trusted-add-email")
        compose.onNodeWithTag("trusted-add-email").performTextInput("alex@other.example")
        compose.onNodeWithTag("trusted-add").performClick()
        compose.until("\"add:alex@other.example\" in store.calls") { "add:alex@other.example" in store.calls }
        scrollTo("trusted-suggestion")
        compose.onNodeWithText("alex.rivera@lab.example").assertExists()
        compose.onNodeWithText("4 emails from it").assertExists()
        compose.onAllNodesWithTag("trusted-remove")[0].performClick()
        compose.onNodeWithTag("trusted-remove-confirm").performClick()
        compose.until("\"remove:1\" in store.calls") { "remove:1" in store.calls }
    }

    @Test fun addressesAnotherAssistantHandles() {
        val store = show()
        compose.onAllNodesWithTag("trusted-mode-ask").assertCountEquals(0)
        scrollTo("elsewhere-email")
        compose.onNodeWithText("None. Standing orders run on mail to all your addresses.").assertExists()
        compose.onNodeWithTag("elsewhere-email").performTextInput("Memory@Example.com")
        compose.onNodeWithTag("elsewhere-add").performClick()
        compose.until("\"elsewhere:memory@example.com\" in store.calls") { "elsewhere:memory@example.com" in store.calls }
        scrollTo("elsewhere-address")
        compose.onNodeWithText("memory@example.com").assertExists()
        compose.onNodeWithTag("elsewhere-remove").performClick()
        compose.until("\"elsewhere:\" in store.calls") { "elsewhere:" in store.calls }
        assertEquals(emptyList<String>(), store.listing.handledElsewhere)
    }

    @Test fun theForwardedNotesOrderIsOneTapAway() {
        show()
        scrollTo("trusted-open-orders")
        compose.onNodeWithTag("trusted-open-orders").performClick()
        compose.waitForIdle()
        assertEquals(1, opened)
    }
}
