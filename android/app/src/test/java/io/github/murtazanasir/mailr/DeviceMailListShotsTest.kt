package io.github.murtazanasir.mailr

import android.app.Application
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.ui.mail.MailDrawer
import io.github.murtazanasir.mailr.ui.mail.Section
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** The mail list: inbox, priority inbox, selection, the shell with its rail, and the drawer. On every device of [DeviceShots], light and dark. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class DeviceMailListShotsTest(device: String, qualifiers: String, dark: Boolean) : DeviceShots(device, qualifiers, dark) {
    @Test fun inbox() = shot("inbox") { List() }

    @Test fun priorityInbox() = shot("priority") {
        List(sections = listOf(Section("important", "Important and unread", Fixtures.conversations.take(2))), title = "Everything else")
    }

    @Test fun selection() = shot("selection") { List(selection = setOf(3074L, 3070L)) }

    @Test fun shell() = shot("shell") {
        // A closed drawer inside the content, as in MailPane: it must not paint over the rail.
        io.github.murtazanasir.mailr.ui.AppNavigation(io.github.murtazanasir.mailr.ui.Tab.MAIL, {}, 7) {
            androidx.compose.material3.ModalNavigationDrawer(
                drawerContent = { MailDrawer(Fixtures.labels, MailView(), null, emptyMap(), "me@example.com", 2, {}, {}, {}) },
            ) { List() }
        }
    }

    @Test fun drawer() = shot("drawer") {
        MailDrawer(Fixtures.labels, MailView(), null, emptyMap(), "me@example.com", 2, {}, {}, {})
    }
}
