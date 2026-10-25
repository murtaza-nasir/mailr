// ACT-SURFACES: android.settings
package io.github.murtazanasir.mailr.ui.settings

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.onFirst
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.api.StandingOrder
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

/** The orders in memory; records every call. */
class FakeOrdersStore : OrdersStore {
    val calls = CopyOnWriteArrayList<String>()
    var orders = listOf(
        StandingOrder(1, "Follow my forwarded notes", builtin = "forwarded_notes", permission = "ask", fromTrusted = true),
        StandingOrder(2, "Invoices", query = "subject:invoice"),
        StandingOrder(3, "Morning", trigger = "schedule", enabled = false),
    )

    override suspend fun list(): List<StandingOrder> { calls += "list"; return orders }
    override suspend fun setEnabled(id: Long, enabled: Boolean) {
        calls += "enabled:$id:$enabled"
        orders = orders.map { if (it.id == id) it.copy(enabled = enabled) else it }
    }
    override suspend fun setPermission(id: Long, permission: String) {
        calls += "permission:$id:$permission"
        orders = orders.map { if (it.id == id) it.copy(permission = permission) else it }
    }
}

/**
 * Settings → Ambrose → Standing orders on a phone: the orders top
 * first, order.pause (each switch), and the built-in order Follow my forwarded notes with order.permission
 * (Ask me → Allow) and its Off state.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class OrdersScreenTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private fun show(store: FakeOrdersStore = FakeOrdersStore()): FakeOrdersStore {
        compose.setContent { MaterialTheme { OrdersPage(store, "Ambrose") {} } }
        compose.until("\"list\" in store.calls") { "list" in store.calls }
        compose.waitForIdle()
        return store
    }

    @Test fun listsTheOrdersWithTheBuiltInOneFirst() {
        show()
        compose.onAllNodesWithTag("order-builtin").assertCountEquals(1)
        compose.onAllNodesWithTag("order").assertCountEquals(2)
        compose.onNodeWithText("Follow my forwarded notes").assertExists()
        compose.onNodeWithText("Mail: subject:invoice").assertExists()
        compose.onNodeWithTag("order-permission-ask").assertExists()
    }

    @Test fun permissionAndSwitches() {
        val store = show()
        compose.onNodeWithTag("order-permission-allow").performClick()
        compose.until("\"permission:1:allow\" in store.calls") { "permission:1:allow" in store.calls }
        assertEquals("allow", store.orders[0].permission)
        compose.onAllNodesWithTag("order-row").onFirst().performClick()
        compose.until("\"enabled:1:false\" in store.calls") { "enabled:1:false" in store.calls }
        compose.waitForIdle()
        compose.onNodeWithText("Off: forwarded notes are ordinary mail.").assertExists()
        compose.onAllNodesWithTag("order-permission-allow").assertCountEquals(0)
        assertTrue(store.calls.none { it.startsWith("delete") })
    }
}
