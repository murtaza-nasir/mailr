// ACT-SURFACES: android.list
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.paging.testing.asSnapshot
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.db.ListItemEntity
import io.github.murtazanasir.mailr.data.db.MailrDb
import io.github.murtazanasir.mailr.data.db.RemoteKeyEntity
import io.github.murtazanasir.mailr.data.repo.MailRepository
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.repo.OfflineSearch
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Search with no network: the conversations cached on the phone (any folder opened before) are
 * searched instead, and the list is marked as offline results until the server answers again.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class OfflineSearchTest {
    private lateinit var db: MailrDb
    private lateinit var repo: MailRepository

    @Before fun setUp() {
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        // Nothing listens there: every request fails as with no network.
        val sessions = SessionStore(ctx).apply { server = "http://127.0.0.1:9"; save("tok", "csrf", "me@example.com", "Me") }
        db = MailrDb.create(ctx, inMemory = true)
        repo = MailRepository(MailrApi(ApiClient(sessions)), db)
    }

    @After fun tearDown() = db.close()

    private suspend fun cacheInbox() {
        val rows = Fixtures.conversations.mapIndexed { i, c ->
            ListItemEntity(MailView().key, c.id, i.toLong(), MailrJson.encodeToString(ConversationSummary.serializer(), c))
        }
        db.lists().replacePage(MailView().key, rows, RemoteKeyEntity(MailView().key, null, rows.size, 0))
    }

    @Test fun aSearchOfflineAnswersFromTheCachedLists() = runTest {
        cacheInbox()
        val view = MailView(q = "pads")
        val found = repo.pager(view).asSnapshot()
        assertEquals(listOf(3066L), found.map { it.id })
        assertTrue(view.key in repo.offlineResults.value)
        // The inbox's own cache is untouched.
        assertEquals(Fixtures.conversations.size, db.lists().first(MailView().key, 50).size)
    }

    @Test fun operatorsTheServerAloneKnowsAreLeftOut() {
        val all = Fixtures.conversations
        assertEquals(listOf(3066L), OfflineSearch.search(all, "\"invites submissions\"").map { it.id })
        assertTrue(OfflineSearch.search(all, "has:attachment").all { it.hasAttachments })
        assertTrue(OfflineSearch.search(all, "is:unread").all { it.unread })
        // newer_than: and -is:read need the server: the words alone decide.
        assertEquals(OfflineSearch.search(all, "booking").map { it.id }, OfflineSearch.search(all, "booking newer_than:7d -is:read").map { it.id })
        assertFalse(OfflineSearch.search(all, "booking").isEmpty())
        assertTrue(OfflineSearch.search(all, "nothing-like-this").isEmpty())
    }
}
