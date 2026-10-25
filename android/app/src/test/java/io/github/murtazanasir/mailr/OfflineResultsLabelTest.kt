package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.SnackbarHostState
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.onNodeWithText
import androidx.paging.PagingData
import androidx.paging.compose.collectAsLazyPagingItems
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.mail.ListActions
import io.github.murtazanasir.mailr.ui.mail.MailListPane
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import kotlinx.coroutines.flow.flowOf
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** A search answered from the phone's cache says so above the results. */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class OfflineResultsLabelTest {
    @get:Rule val compose = createComposeRule()

    @Test fun offlineResultsAreLabelled() {
        val noList = ListActions({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> }, {}, { _, _ -> }, {}, {}, {})
        compose.setContent {
            MailrTheme(ThemeMode.LIGHT, dynamic = false) {
                val items = flowOf(PagingData.from(Fixtures.conversations.take(2))).collectAsLazyPagingItems()
                MailListPane(MailView(q = "pads"), items, emptyList(), null, Fixtures.labels, emptySet(), null, Prefs(),
                    "Alex Morgan", "me@example.com", false, 0, SnackbarHostState(), true, noList, offlineResults = true)
            }
        }
        compose.onNodeWithText("Offline results: mail on this phone only").assertIsDisplayed()
    }
}
