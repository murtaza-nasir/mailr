// ACT-SURFACES: android.noteswidget
package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.notes.FakeNotesServer
import io.github.murtazanasir.mailr.notes.grammar.ItemGrammar
import io.github.murtazanasir.mailr.notesapp.capture.CaptureActivity
import io.github.murtazanasir.mailr.notesapp.capture.CaptureLogic
import io.github.murtazanasir.mailr.notesapp.capture.VoicePhrase
import io.github.murtazanasir.mailr.notesapp.capture.VoicePhrase.Parsed
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * checkitem.voice_add: the spoken-phrase grammar (fixed rules, no model) that turns "add milk
 * and eggs to shopping" into a list and its items, refuses to guess an unknown list, and the add itself
 * through the repository's queue (offline too). The widget's microphone opens it on-device only.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class VoicePhraseTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = NotesTestGraph()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: NotesGraph

    private val shopping = ItemGrammar.ListInfo("n1", "Shopping", emptyList())
    private val costco = ItemGrammar.ListInfo("n2", "Costco", emptyList())
    private val packing = ItemGrammar.ListInfo("n3", "Packing list", emptyList())
    private val corner = ItemGrammar.ListInfo("n4", "Corner market", emptyList())
    private val lists = listOf(shopping, costco, packing, corner)

    @Before fun setUp() {
        fake = graphs.closeLast(object : AutoCloseable {
            val f = FakeNotesServer().start()
            override fun close() = f.close()
        }).f
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        SessionStore(ctx).apply {
            server = fake.server.url("/").toString().trimEnd('/')
            save("tok-1", "csrf-1", "me@example.com", "Me")
        }
        g = graphs.graph(ctx)
    }

    private fun p(s: String) = VoicePhrase.parse(s, lists)

    @Test fun phrasesThatNameAList() {
        assertEquals(Parsed.Add(shopping, listOf("milk", "eggs")), p("add milk and eggs to shopping"))
        assertEquals(Parsed.Add(costco, listOf("bread")), p("put bread on the Costco list"))
        assertEquals(Parsed.Add(shopping, listOf("milk")), p("Shopping: milk"))
        assertEquals(Parsed.Add(shopping, listOf("2 milk", "eggs", "bread")), p("Please add 2 milk, eggs and bread to my shopping list."))
        assertEquals(Parsed.Add(corner, listOf("limes")), p("add limes to corner market"))
        // A list named "Packing list", said with or without the word.
        assertEquals(Parsed.Add(packing, listOf("socks")), p("add socks to the packing list"))
        assertEquals(Parsed.Add(packing, listOf("charger")), p("put charger in packing"))
        // Every "to" from the right: the one whose name is a list wins.
        assertEquals(Parsed.Add(shopping, listOf("bread to go")), p("add bread to go to shopping"))
    }

    @Test fun anUnknownListIsNeverGuessed() {
        assertEquals(Parsed.UnknownList("groceries", listOf("bananas")), p("add bananas to groceries"))
        // "shop" is the start of Shopping, but not its name.
        assertEquals(Parsed.UnknownList("shop", listOf("milk")), p("add milk to the shop list"))
        // A name before a colon that is no list is not a list phrase: it stays words for a note.
        assertEquals(Parsed.NotAList("Note to self: call Dana"), p("Note to self: call Dana"))
    }

    @Test fun phrasesWithoutAList() {
        assertEquals(Parsed.NoList(listOf("milk", "eggs")), p("add milk and eggs"))
        assertEquals(Parsed.NotAList("call the plumber"), p("call the plumber"))
        assertEquals(Parsed.NotAList(""), p("   "))
        assertEquals("milk, eggs and bread", VoicePhrase.join(listOf("milk", "eggs", "bread")))
    }

    @Test fun theAddGoesThroughTheQueueOffline() = runBlocking {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        fake.note("checklist", "Hardware")
        val repo = g.notes.repo
        repo.pull()
        val mine = CaptureLogic.lists(repo.notes.first(), emptyMap())
        fake.offline = true
        // checkitem.voice_add: on the list at once, queued for the server.
        val out = VoicePhrase.apply(repo, "add eggs and bread to shopping", mine, null)
        assertEquals("Added eggs and bread to Shopping", out.message)
        assertEquals(shop.id, out.listId)
        assertEquals(listOf("milk", "eggs", "bread"), repo.itemsNow(shop.id).sortedBy { it.order }.map { it.text })
        assertEquals("No list called “groceries”", VoicePhrase.apply(repo, "add bananas to groceries", mine, null).message)
        // From the list's own widget: plain words are its items; from the capture widget they become a note.
        assertEquals("Added nails to Hardware", VoicePhrase.apply(repo, "nails", mine, mine.single { it.name == "Hardware" }.id).message)
        val note = VoicePhrase.apply(repo, "call the plumber", mine, null)
        assertNull(note.message)
        assertEquals("call the plumber", note.noteText)
        fake.offline = false
        repo.drain()
        assertTrue(fake.items[shop.id]!!.map { it.text }.containsAll(listOf("eggs", "bread")))
    }

    @Test fun theWidgetOpensVoiceAddForItsList() {
        val i = CaptureActivity.intent(ApplicationProvider.getApplicationContext(), "voice", "n1")
        assertEquals("voice", i.getStringExtra(CaptureActivity.EXTRA_MODE))
        assertEquals("n1", i.getStringExtra(CaptureActivity.EXTRA_LIST))
    }
}
