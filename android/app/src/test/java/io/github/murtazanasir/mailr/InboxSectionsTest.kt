// ACT-SURFACES: android.list
package io.github.murtazanasir.mailr

import android.app.Application
import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.mail.MailboxViewModel
import io.github.murtazanasir.mailr.ui.mail.SECTION_GROWN
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Inbox sections on the phone: a capped section knows how many
 * it holds; "Show more" (layout.section_more) asks for more rows and "Show fewer" goes back to the
 * cap; "Show only this section" (layout.section_focus) makes the section the tab's paged list and
 * hides the others, until Back; another tab shows all sections again.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class InboxSectionsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    private lateinit var server: MockWebServer
    private lateinit var g: AppGraph
    private val requests = CopyOnWriteArrayList<RecordedRequest>()

    private val layouts = """{"active":"two","layouts":[{"id":"two","name":"Two","tabs":[
        {"id":"inbox","name":"Inbox","icon":"inbox","sections":[
          {"id":"unread","title":"Unread","base":"inbox","filter":"unread","limit":3},
          {"id":"rest","title":"Everything else","base":"inbox","exclude_above":true}]},
        {"id":"later","name":"Later","icon":"tag","sections":[{"id":"later","title":"Later","base":"inbox"}]}]}]}"""

    private fun page(n: Int, total: Int, from: Long) =
        """{"items":[${(0 until n).joinToString(",") { """{"id":${from + it},"subject":"Mail ${from + it}"}""" }}],"total":$total}"""

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request
                val url = request.requestUrl!!
                val path = url.encodedPath
                return when {
                    path == "/api/v1/me" -> MockResponse().setBody("""{"user":{"id":1,"email":"me@example.com","name":"Me"},"identities":[]}""")
                    path == "/api/v1/layouts" -> MockResponse().setBody(layouts)
                    path == "/api/v1/conversations" -> {
                        val limit = url.queryParameter("limit")?.toInt() ?: 50
                        when (url.queryParameter("sec")) {
                            "unread" -> MockResponse().setBody(page(minOf(limit, 8), 8, 100))
                            else -> MockResponse().setBody(page(minOf(limit, 20), 20, 200))
                        }
                    }
                    path.startsWith("/api/v1/labels") -> MockResponse().setBody("[]")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        server.start()
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        SessionStore(ctx).apply {
            server = this@InboxSectionsTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
        g = graphs.graph(ctx)
    }

    @After fun tearDown() {
        shadowOf(Looper.getMainLooper()).idle()
        graphs.close()
        server.shutdown()
    }

    private fun limitsFor(sec: String) = requests.mapNotNull { r ->
        r.requestUrl?.takeIf { it.encodedPath == "/api/v1/conversations" && it.queryParameter("sec") == sec }?.queryParameter("limit")?.toInt()
    }

    @Test fun aCappedSectionKnowsItsTotalAndGrowsInPlace() {
        val vm = graphs.vm { MailboxViewModel(g) }
        eventually("sections") { vm.sections.value.isNotEmpty() }
        val s = vm.sections.value.single()
        assertEquals("unread", s.key)
        assertEquals(3, s.items.size)
        assertEquals(8, s.total)
        assertEquals(3, s.cap)
        assertEquals("Everything else", vm.sectionTitle.value)
        assertEquals("rest", vm.mainSection.value)

        vm.toggleGrown("unread")
        eventually("grown") { vm.sections.value.singleOrNull()?.expanded == true && vm.sections.value.single().items.size == 8 }
        assertTrue(SECTION_GROWN in limitsFor("unread"))

        vm.toggleGrown("unread")
        eventually("fewer") { vm.sections.value.singleOrNull()?.let { !it.expanded && it.items.size == 3 } == true }
    }

    @Test fun aSectionShownAloneHidesTheOthersUntilBackOrAnotherTab() {
        val vm = graphs.vm { MailboxViewModel(g) }
        eventually("sections") { vm.sections.value.isNotEmpty() }

        vm.focusSection("unread")
        eventually("focused") { vm.focusedSection.value == "unread" && vm.sections.value.isEmpty() }
        assertEquals("Unread", vm.sectionTitle.value)

        vm.unfocusSection()
        eventually("all again") { vm.focusedSection.value == null && vm.sections.value.isNotEmpty() }
        assertEquals("Everything else", vm.sectionTitle.value)

        vm.focusSection("rest")
        eventually("main alone") { vm.focusedSection.value == "rest" && vm.sections.value.isEmpty() }
        vm.open(io.github.murtazanasir.mailr.data.repo.MailView(tab = "later"))
        eventually("other tab") { vm.focusedSection.value == null }
        assertNull(vm.focusedSection.value)
    }
}
