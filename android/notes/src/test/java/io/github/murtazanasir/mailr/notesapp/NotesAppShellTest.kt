// ACT-SURFACES: android.notesapp android.noteslauncher
package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.notes.FakeNotesServer
import io.github.murtazanasir.mailr.notes.ui.NotesPage
import io.github.murtazanasir.mailr.notesapp.ui.LinkRoute
import io.github.murtazanasir.mailr.notesapp.ui.NotesAppRoot
import io.github.murtazanasir.mailr.notesapp.ui.resolveLink
import io.github.murtazanasir.mailr.testing.eventually
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The Notes app's shell: sign-in with the app's own name, the grid at once from the phone's copy
 * (the first frame does not wait for the network), and deep-link routing (note.open_app): a note the
 * account can see opens, an item of another account shows "Not available to <account>", an item in
 * Trash opens in its Trash state, an https link for another server is refused, and New note and New
 * list (the launcher's static shortcuts, note.create) open the editor at once.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class NotesAppShellTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = NotesTestGraph()
    @get:Rule val compose = createComposeRule()
    private lateinit var fake: FakeNotesServer
    private val ctx get() = ApplicationProvider.getApplicationContext<Application>()

    @Before fun setUp() {
        fake = graphs.closeLast(object : AutoCloseable {
            val f = FakeNotesServer().start()
            override fun close() = f.close()
        }).f
    }

    private fun signIn() = SessionStore(ctx).apply {
        server = fake.server.url("/").toString().trimEnd('/')
        save("tok-1", "csrf-1", "me@example.com", "Me")
    }

    private fun waitText(t: String, substring: Boolean = false) = compose.eventually(t) {
        compose.onAllNodes(hasText(t, substring = substring)).fetchSemanticsNodes().isNotEmpty()
    }

    @Test fun signedOutShowsTheNotesSignIn() {
        SessionStore(ctx).apply { server = "https://mail.example.com" }
        val g = graphs.graph(ctx)
        compose.setContent { NotesAppRoot(g, MutableStateFlow(null)) {} }
        waitText("Notes, lists and tables on your Mailr server")
        compose.onNodeWithText("Connect to your server").assertExists()
    }

    @Test fun startsOnTheGridFromTheCacheWithNoNetwork() {
        signIn()
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        val g = graphs.graph(ctx)
        runBlocking { g.notes.repo.pull() }
        // The server is gone: the grid shows from the phone's copy all the same.
        fake.offline = true
        compose.setContent { NotesAppRoot(g, MutableStateFlow(null)) {} }
        waitText("Shopping")
        compose.eventually("the card") { compose.onAllNodes(hasTestTag("card-${shop.id}")).fetchSemanticsNodes().isNotEmpty() }
    }

    @Test fun deepLinksRouteByWhatTheAccountSees() = runBlocking {
        val s = signIn()
        val g = graphs.graph(ctx)
        val mine = fake.note("text", "Plan")
        val old = fake.note("text", "Old").also { it.deleted = "2026-10-04T10:00:00+00:00" }
        val t = fake.table("Expenses", listOf("Item" to "text"), listOf(mapOf("c0" to "Rent")))
        g.notes.repo.pull()
        val session = s.session.value!!
        // A visible note (on the phone) and one read from the server on demand.
        assertEquals(LinkRoute.Open(listOf(NotesPage.OpenNote(mine.id, "i1"))),
            resolveLink(g, DeepLinks.parse("mailr-notes://note/${mine.id}/item/i1")!!, session))
        val later = fake.note("checklist", "Made on the web")
        assertEquals(LinkRoute.Open(listOf(NotesPage.OpenNote(later.id))), resolveLink(g, DeepLinks.parse(DeepLinks.noteUri(later.id))!!, session))
        // An item of another account: the server says 404.
        assertEquals(LinkRoute.Unavailable("Not available to me@example.com"),
            resolveLink(g, DeepLinks.parse("mailr-notes://note/n999")!!, session))
        // An item in Trash opens (the editor shows its Trash state and Restore).
        assertEquals(LinkRoute.Open(listOf(NotesPage.OpenNote(old.id))), resolveLink(g, DeepLinks.parse(DeepLinks.noteUri(old.id))!!, session))
        // A table, a row, and the table widget's new row.
        val row = fake.rows.values.single().id
        assertEquals(LinkRoute.Open(listOf(NotesPage.OpenTable(t.id), NotesPage.OpenRow(t.id, row))),
            resolveLink(g, DeepLinks.parse(DeepLinks.tableUri(t.id, row))!!, session))
        assertEquals(LinkRoute.Open(listOf(NotesPage.OpenTable(t.id), NotesPage.OpenRow(t.id, null))),
            resolveLink(g, DeepLinks.parse(DeepLinks.tableUri(t.id, "new"))!!, session))
        // An https link to the signed-in server opens; to another server it does not.
        val host = fake.server.hostName
        assertEquals(LinkRoute.Open(listOf(NotesPage.OpenNote(mine.id))),
            resolveLink(g, NotesLink(NotesTarget.Note(mine.id), host), session))
        assertEquals(LinkRoute.Unavailable("Not available to me@example.com"),
            resolveLink(g, DeepLinks.parse("https://mail.other.example/notes/n/${mine.id}")!!, session))
        // Offline, and not on the phone.
        fake.offline = true
        val r = resolveLink(g, DeepLinks.parse("mailr-notes://note/n555")!!, session)
        assertTrue(r is LinkRoute.Unavailable && r.message.contains("Connect"))
    }

    @Test fun aLinkOpensTheNoteAndAnUnknownOneSaysSo() {
        signIn()
        val g = graphs.graph(ctx)
        val mine = fake.note("text", "Plan").also { it.body = "paint the shed" }
        runBlocking { g.notes.repo.pull() }
        val links = MutableStateFlow<NotesLink?>(DeepLinks.parse(DeepLinks.noteUri(mine.id)))
        compose.setContent { NotesAppRoot(g, links) { links.value = null } }
        waitText("paint the shed")
        compose.runOnIdle { links.value = DeepLinks.parse("mailr-notes://note/n999") }
        waitText("Not available to me@example.com")
        compose.onNodeWithText("Back to notes").assertExists()
    }

    @Test fun newNoteAndNewListShortcutsOpenTheEditor() {
        signIn()
        val g = graphs.graph(ctx)
        val links = MutableStateFlow<NotesLink?>(DeepLinks.parse(DeepLinks.newUri("checklist")))
        compose.setContent { NotesAppRoot(g, links) { links.value = null } }
        // note.create from the static "New list" shortcut: a list exists on the phone at once, and its editor is open.
        compose.eventually("a new list") { kinds(g) == listOf("checklist") }
        compose.eventually("its add line") { compose.onAllNodes(hasTestTag("add-top")).fetchSemanticsNodes().isNotEmpty() }
        compose.runOnIdle { links.value = DeepLinks.parse(DeepLinks.newUri("text")) }
        compose.eventually("a new note") { "text" in kinds(g) }
        compose.eventually("its body field") { compose.onAllNodes(hasTestTag("note-body")).fetchSemanticsNodes().isNotEmpty() }
    }

    private fun kinds(g: NotesGraph) = runBlocking { g.notes.repo.notes.first().map { it.kind } }
}
