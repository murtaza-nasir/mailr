// ACT-SURFACES: android.assistant
package io.github.murtazanasir.mailr.ui.assistant

import android.app.Application
import android.os.Looper
import androidx.activity.OnBackPressedDispatcher
import androidx.activity.compose.LocalOnBackPressedDispatcherOwner
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performImeAction
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import androidx.lifecycle.SavedStateHandle
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import kotlinx.coroutines.launch
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.Collections

/**
 * Slash commands in the Ambrose chat (agent.commands, as the web's commands.ts and SkillPicker.tsx).
 * The picker: a `/` that opens the message lists the commands, then the skills, filtered as the name is
 * typed; `//` lists the skills only; a tap puts `/name ` in the box, or sends a no-text command typed in
 * full; Back or deleting the `/` closes it. The commands: the app ones run here (`/new`, `/help`,
 * `/local`, `/rename`, `/retitle` (agent.chat_retitle), `/retry`, `/copy`, `/note` (note.from_answer),
 * `/delete`, `/chats`); the others go as the message's text, and the server's own reply (`error` with code
 * `command`) shows as an answer, not a failure. The picker's lists are read again when it opens, at most
 * every 30 s, and at once after the chat made or changed a skill.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ChatCommandsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private val app: Application = ApplicationProvider.getApplicationContext()
    private var server: MockWebServer? = null
    /** "METHOD path body" of every request the fake server got. */
    private val requests: MutableList<String> = Collections.synchronizedList(mutableListOf())
    /** The next chat answer: a normal one, or the server's reply to a command. */
    @Volatile private var commandReply: String? = null
    /** POST /notes fails; POST /notes/{id}/links fails. */
    @Volatile private var noteFails = false
    @Volatile private var linkFails = false
    /** The next answer makes a skill (a create_skill step). */
    @Volatile private var makesSkill = false

    companion object {
        val COMMANDS = listOf(
            ChatCommand("new", listOf("clear"), "app", "chat", summary = "Start a new chat"),
            ChatCommand("chats", kind = "app", group = "chat", arg = "optional", summary = "Open your chats, or search them"),
            ChatCommand("rename", kind = "app", group = "chat", arg = "required", summary = "Rename this chat", example = "/rename Fitness log"),
            ChatCommand("retitle", kind = "app", group = "chat", summary = "Make a new short title for this chat"),
            ChatCommand("delete", kind = "app", group = "chat", summary = "Delete this chat"),
            ChatCommand("retry", kind = "app", group = "chat", summary = "Write the last answer again"),
            ChatCommand("copy", kind = "app", group = "chat", summary = "Copy the last answer"),
            ChatCommand("note", kind = "app", group = "chat", summary = "Save the last answer to a note"),
            ChatCommand("local", kind = "app", group = "chat", summary = "Keep this chat on your local model"),
            ChatCommand("model", kind = "info", group = "chat", summary = "Show which model answers in this chat"),
            ChatCommand("help", kind = "app", group = "chat", summary = "List the commands and your skills"),
            ChatCommand("task", kind = "ask", group = "add", arg = "required", summary = "Add a task", example = "/task call the bank fri 3pm"),
            ChatCommand("today", kind = "ask", group = "ask", summary = "Today: calendar, tasks due and mail that needs you"),
            ChatCommand("reply", kind = "ask", group = "ask", arg = "optional", summary = "Draft a reply to the open conversation"),
        )
        val SKILLS = listOf(
            SkillLite("formal", "Formal", "A formal, polite register"),
            SkillLite("retro", "Retro", ""),
            SkillLite("email-basics", "Email basics", "The writing defaults", auto = true),
            SkillLite("off", "Off", "Turned off", enabled = false),
        )
    }

    @After fun tearDown() {
        shadowOf(Looper.getMainLooper()).idle()
        graphs.close()
        server?.shutdown()
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idleFor(java.time.Duration.ofMillis(10))

    private fun until(what: String, cond: () -> Boolean) = eventually(what, idle = ::idle, detail = { "requests=$requests" }, check = cond)

    /** Sends [command] and waits for a new turn whose answer is [answer]. */
    private fun answers(vm: AssistantViewModel, command: String, answer: String) {
        val before = vm.state.value.turns.size
        vm.send(command)
        until(answer) { vm.state.value.turns.size > before && vm.state.value.turns.last().answer == answer }
    }

    private val commandsJson = kotlinx.serialization.json.Json.encodeToString(CommandList.serializer(), CommandList(COMMANDS))
    @Volatile private var skillsJson = """{"items":[{"name":"formal","title":"Formal","description":"A formal, polite register","enabled":true,"auto":false}]}"""

    private fun sse(vararg events: Pair<String, String>) = MockResponse().setHeader("Content-Type", "text/event-stream")
        .setBody(events.joinToString("") { (t, d) -> "event: $t\ndata: $d\n\n" })

    private fun serve() {
        val s = MockWebServer()
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = request.body.readUtf8()
                requests += "${request.method} ${request.path} $body".trim()
                return when {
                    request.path == "/api/v1/agent/commands" -> MockResponse().setBody(commandsJson)
                    request.path == "/api/v1/assistant/skills" -> MockResponse().setBody(skillsJson)
                    request.method == "POST" && request.path == "/api/v1/agent/sessions/c9/retitle" ->
                        MockResponse().setBody("""{"id":"c9","title":"Dana the accountant","renamed":false,"previous":{"title":"Who is Dana?","renamed":false}}""")
                    request.method == "DELETE" && request.path == "/api/v1/agent/sessions/c9" -> MockResponse().setBody("{}")
                    request.method == "PATCH" && request.path == "/api/v1/agent/sessions/c9" ->
                        MockResponse().setBody("""{"id":"c9","title":"Dana","renamed":true,"previous":{"title":"Who is Dana?","renamed":false}}""")
                    request.method == "POST" && request.path == "/api/v1/notes" ->
                        if (noteFails) MockResponse().setResponseCode(500).setBody("""{"detail":"boom"}""")
                        else MockResponse().setBody("""{"note":{"id":"n77","title":"Dana is your accountant.","kind":"text"},"undo":null}""")
                    request.method == "POST" && request.path == "/api/v1/notes/n77/links" ->
                        if (linkFails) MockResponse().setResponseCode(404).setBody("""{"detail":"gone"}""") else MockResponse().setBody("{}")
                    request.method == "POST" && request.path == "/api/v1/agent/chat" && makesSkill -> sse(
                        "session" to """{"session_id":"c9","title":"Make a skill","user_message_id":43,"seq":1}""",
                        "tool" to """{"id":"s1","name":"create_skill","label":"Saving a new skill","ok":true,"summary":"saved","segment":0,"seq":2}""",
                        "final" to """{"content":"I made /standup.","citations":[],"segment":0,"seq":3}""",
                        "done" to """{"stopped":false,"seq":4}""")
                    request.method == "POST" && request.path == "/api/v1/agent/chat" -> commandReply?.let { reply ->
                        sse("error" to """{"code":"command","message":"$reply","seq":1}""")
                    } ?: sse(
                        "session" to """{"session_id":"c9","title":"Who is Dana?","user_message_id":41,"seq":1}""",
                        "final" to """{"content":"Dana is your accountant [1](mailr-cite:1).","citations":[{"n":1,"kind":"message","conversation_id":5,"subject":"Invoice","from":"Dana Whitfield","date":"2026-10-01T09:00:00Z"}],"segment":0,"seq":2}""",
                        "done" to """{"stopped":false,"seq":3}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        s.start()
        server = s
        SessionStore(app).apply { server = s.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
    }

    private fun model(): AssistantViewModel {
        serve()
        val g = graphs.graph(app)
        return graphs.vm { AssistantViewModel(g, SavedStateHandle()) }.also { vm ->
            vm.loadCommands()
            until("commands loaded") { vm.state.value.commands.isNotEmpty() && vm.state.value.skills.isNotEmpty() }
        }
    }

    /** A chat with one answer (session c9, its question stored as message 41). */
    private fun answered(): AssistantViewModel = model().also { vm ->
        vm.send("Who is Dana?")
        until("answered") { !vm.state.value.busy && vm.state.value.turns.size == 2 && vm.state.value.sessionId == "c9" }
    }

    private fun chatPosts() = requests.filter { it.startsWith("POST /api/v1/agent/chat ") }

    // The picker's rows.

    @Test fun theCommandsComeFirstThenTheSkillsFilteredAsTheNameIsTyped() {
        val all = ChatCommands.picker("/", COMMANDS, SKILLS)
        assertEquals(12, all.size)
        assertTrue(all.all { it.command })
        val re = ChatCommands.picker("/re", COMMANDS, SKILLS)
        assertEquals(listOf("rename", "retitle", "retry", "reply", "retro"), re.map { it.name })
        assertEquals(listOf(true, true, true, true, false), re.map { it.command })
        // A skill with no description shows its title; automatic and turned-off skills are not listed.
        assertEquals("Retro", re.last().desc)
        assertTrue(ChatCommands.picker("/e", COMMANDS, SKILLS).isEmpty())
        assertTrue(ChatCommands.picker("/of", COMMANDS, SKILLS).isEmpty())
        // An alias finds its command; the case of what is typed does not matter.
        assertEquals(listOf("new"), ChatCommands.picker("/cle", COMMANDS, SKILLS).map { it.name })
        assertEquals(listOf("formal"), ChatCommands.picker("/FOR", COMMANDS, SKILLS).map { it.name })
    }

    @Test fun twoSlashesListTheSkillsOnly() {
        assertEquals(listOf("formal", "retro"), ChatCommands.picker("//", COMMANDS, SKILLS).map { it.name })
        assertEquals(listOf("retro"), ChatCommands.picker("//re", COMMANDS, SKILLS).map { it.name })
    }

    @Test fun thePickerIsClosedOnceTheMessageGoesOnOrDoesNotOpenWithASlash() {
        assertTrue(ChatCommands.picker("", COMMANDS, SKILLS).isEmpty())
        assertTrue(ChatCommands.picker("/task ", COMMANDS, SKILLS).isEmpty())
        assertTrue(ChatCommands.picker("/task call", COMMANDS, SKILLS).isEmpty())
        assertTrue(ChatCommands.picker("ask /re", COMMANDS, SKILLS).isEmpty())
        assertTrue(ChatCommands.picker("/re", emptyList(), emptyList()).isEmpty())
    }

    @Test fun aNoTextCommandTypedInFullIsComplete() {
        assertTrue(ChatCommands.picker("/new", COMMANDS, SKILLS).first { it.name == "new" }.complete)
        assertTrue(ChatCommands.picker("/clear", COMMANDS, SKILLS).single().complete)
        assertFalse(ChatCommands.picker("/ne", COMMANDS, SKILLS).single().complete)
        // A command that takes text is never complete: it is inserted, for the text after it.
        assertFalse(ChatCommands.picker("/rename", COMMANDS, SKILLS).single().complete)
        assertFalse(ChatCommands.picker("/chats", COMMANDS, SKILLS).single().complete)
        assertEquals("/rename ", ChatCommands.insert(ChatCommands.picker("/ren", COMMANDS, SKILLS).single()))
    }

    @Test fun aMessageIsReadAsTheServerReadsIt() {
        assertEquals("task" to "call the bank fri 3pm", ChatCommands.parse("  /task call the bank fri 3pm ", COMMANDS)?.let { it.first.name to it.second })
        assertEquals("new", ChatCommands.parse("/CLEAR", COMMANDS)?.first?.name)
        assertNull(ChatCommands.parse("/formal write it", COMMANDS))
        assertNull(ChatCommands.parse("//new", COMMANDS))
        assertNull(ChatCommands.parse("what is /new?", COMMANDS))
        assertNull(ChatCommands.parse("/new", emptyList()))
    }

    @Test fun helpListsTheCommandsByGroupThenTheSkills() {
        val help = ChatCommands.helpText(COMMANDS, SKILLS)
        assertTrue(help, help.startsWith("**This chat**\n- `/new` (`/clear`) — Start a new chat\n"))
        assertTrue(help, "**Add**\n- `/task` — Add a task" in help)
        assertTrue(help, "**Ask**\n- `/today`" in help)
        assertTrue(help, "**Skills**\n- `/formal` — A formal, polite register\n- `/retro` — Retro" in help)
        assertFalse(help, "email-basics" in help)
        assertTrue(help, help.endsWith("Type / at the start of a message to see these as you write."))
    }

    @Test fun copyKeepsTheCitationNumbersAndListsTheSources() {
        val text = ChatCommands.copyText("Dana is your accountant [1](mailr-cite:1).",
            listOf(Citation(1, conversationId = 5, subject = "Invoice", from = "Dana Whitfield", date = "2026-10-01T09:00:00Z"),
                Citation(2, conversationId = 6, subject = "Unused")))
        assertEquals("Dana is your accountant [1].\n\nSources:\n[1] Invoice — Dana Whitfield (2026-10-01)", text)
        assertEquals("No sources.", ChatCommands.copyText(" No sources. ", emptyList()))
    }

    // The commands.

    @Test fun helpIsAnsweredInTheAppAndNotSent() {
        val vm = model()
        vm.send("/help")
        val turns = vm.state.value.turns
        assertEquals(listOf("user", "assistant"), turns.map { it.role })
        assertEquals("/help", turns[0].text)
        assertTrue(turns[1].answer, turns[1].answer.startsWith("**This chat**"))
        assertNull(turns[1].error)
        assertFalse(vm.state.value.busy)
        assertTrue(chatPosts().isEmpty())
    }

    @Test fun anAskCommandGoesAsTheMessage() {
        val vm = model()
        vm.send("/today")
        until("answered") { !vm.state.value.busy && vm.state.value.turns.size == 2 }
        assertTrue(chatPosts().single(), chatPosts().single().contains(""""message":"/today""""))
        assertEquals("/today", vm.state.value.turns[0].text)
    }

    @Test fun theServersReplyToACommandShowsAsAnAnswer() {
        val vm = model()
        commandReply = "This chat uses Qwen, a local model on your own server."
        vm.send("/model")
        until("answered") { !vm.state.value.busy && vm.state.value.turns.size == 2 }
        val t = vm.state.value.turns[1]
        assertNull(t.error)
        assertEquals("This chat uses Qwen, a local model on your own server.", t.answer)
        assertTrue(chatPosts().single().contains(""""message":"/model""""))
    }

    @Test fun newStartsANewChat() {
        val vm = answered()
        vm.send("/clear")
        assertTrue(vm.state.value.turns.isEmpty())
        assertNull(vm.state.value.sessionId)
        assertEquals(1, chatPosts().size)
        // The picker's lists stay.
        assertEquals(COMMANDS.size, vm.state.value.commands.size)
    }

    @Test fun localKeepsTheChatOnTheLocalModelFromTheNextMessage() {
        val vm = model()
        vm.send("/local")
        assertTrue(vm.state.value.localOnly)
        assertEquals("From your next message, Ambrose answers this chat on your local model only.", vm.state.value.turns[1].answer)
        vm.send("/local")
        assertEquals("This chat already stays on your local model.", vm.state.value.turns[3].answer)
        assertTrue(chatPosts().isEmpty())
        vm.send("Who is Dana?")
        until("answered") { !vm.state.value.busy && vm.state.value.sessionId == "c9" }
        assertTrue(chatPosts().single(), chatPosts().single().contains(""""local_only":true"""))
        // A new chat is not local.
        vm.newChat()
        assertFalse(vm.state.value.localOnly)
    }

    @Test fun commandsForTheOpenChatSayWhenThereIsNone() {
        val vm = model()
        answers(vm, "/copy", "There is no answer in this chat yet.")
        answers(vm, "/note", "There is no answer in this chat yet.")
        answers(vm, "/rename Taxes", "Start a chat first: /rename works on the open chat.")
        answers(vm, "/retitle", "Start a chat first: /retitle works on the open chat.")
        answers(vm, "/delete", "Start a chat first: /delete works on the open chat.")
        assertTrue(chatPosts().isEmpty())
        assertFalse(requests.any { it.startsWith("POST /api/v1/notes") })
    }

    @Test fun renameAndRetitleChangeTheOpenChatsTitle() {
        val vm = answered()
        vm.send("/rename")
        until("rename needs a title") { vm.state.value.turns.lastOrNull()?.answer == "Type what you mean after /rename, for example: /rename Fitness log" }
        vm.send("/rename Dana")
        until("renamed") { requests.any { it.startsWith("PATCH /api/v1/agent/sessions/c9") } && vm.state.value.title == "Dana" }
        vm.send("/retitle")
        until("retitled") { vm.state.value.title == "Dana the accountant" }
        assertFalse(vm.state.value.titleRenamed)
        assertTrue(requests.any { it.startsWith("POST /api/v1/agent/sessions/c9/retitle") })
    }

    @Test fun retryWritesTheLastAnswerAgainFromItsQuestion() {
        val vm = answered()
        assertEquals(41L, vm.state.value.turns[0].messageId)
        vm.send("/retry")
        until("answered again") { !vm.state.value.busy && chatPosts().size == 2 }
        assertTrue(chatPosts()[1], chatPosts()[1].contains(""""rerun_from":41"""))
        assertFalse(chatPosts()[1].contains("/retry"))
        // The chat is the question and its new answer: no "/retry" bubble.
        assertEquals(listOf("Who is Dana?"), vm.state.value.turns.filter { it.role == "user" }.map { it.text })
        assertEquals(2, vm.state.value.turns.size)
    }

    @Test fun copyPutsTheLastAnswerOnTheClipboard() {
        val vm = answered()
        vm.send("/copy")
        val cm = app.getSystemService(android.content.ClipboardManager::class.java)
        assertEquals("Dana is your accountant [1].\n\nSources:\n[1] Invoice — Dana Whitfield (2026-10-01)",
            cm.primaryClip!!.getItemAt(0).text.toString())
        // Nothing shows in the chat; the snackbar says it is copied.
        assertEquals(2, vm.state.value.turns.size)
    }

    // `/note` (note.from_answer), as the web's saveAnswerToNote.

    @Test fun noteSavesTheLastAnswerToATextNoteLinkedToItsSources() {
        val vm = answered()
        val seen = Collections.synchronizedList(mutableListOf<ChatsMessage>())
        graphs.scope().launch { vm.renames.collect { seen += it } }
        vm.send("/note")
        until("saved") { seen.isNotEmpty() }
        val made = requests.single { it.startsWith("POST /api/v1/notes ") }
        assertEquals("""POST /api/v1/notes {"kind":"text","title":"Dana is your accountant.","body":"Dana is your accountant [1].\n\nSources:\n[1] Invoice — Dana Whitfield (2026-10-01)"}""", made)
        assertEquals("""POST /api/v1/notes/n77/links {"target_kind":"conversation","target_ref":{"conversation_id":5}}""",
            requests.single { it.startsWith("POST /api/v1/notes/n77/links") })
        assertEquals("Saved to a note", seen[0].text)
        assertEquals("n77", seen[0].openNote)
        // Nothing goes to Ambrose and nothing shows in the chat.
        assertEquals(1, chatPosts().size)
        assertEquals(2, vm.state.value.turns.size)
    }

    @Test fun aNoteWhoseLinksCannotBeMadeSaysHowMany() {
        val vm = answered()
        linkFails = true
        val seen = Collections.synchronizedList(mutableListOf<ChatsMessage>())
        graphs.scope().launch { vm.renames.collect { seen += it } }
        vm.send("/note")
        until("saved") { seen.isNotEmpty() }
        assertEquals("Saved to a note (1 link couldn't be made)", seen[0].text)
        assertEquals("n77", seen[0].openNote)
    }

    @Test fun aNoteThatCannotBeSavedSaysSo() {
        val vm = answered()
        noteFails = true
        val notices = Collections.synchronizedList(mutableListOf<String>())
        graphs.scope().launch { vm.notices.collect { notices += it } }
        vm.send("/note")
        until("failed") { notices.isNotEmpty() }
        assertEquals("Couldn't save the answer to a note", notices[0])
        assertFalse(requests.any { it.contains("/links") })
    }

    @Test fun theNoteOfAnAnswerHasPlainWordsForItsTitleAndALinkPerCitedRecord() {
        val md = "## **Your trip** to [Chicago](https://example.com) [1](mailr-cite:1)\n\nThe train [2](mailr-cite:2), the hotel [3](mailr-cite:3), " +
            "the call [4](mailr-cite:4) and [5](mailr-cite:5), again [1](mailr-cite:1), a page [6](mailr-cite:6)."
        val cites = listOf(
            Citation(1, "message", conversationId = 5, subject = "Tickets"),
            Citation(2, "event", uid = "ev1", occurrence = "2026-10-20", title = "Train"),
            Citation(3, "task", uid = "t1", title = "Book the hotel"),
            Citation(4, "recording", connectorId = 2, recordingId = 9, title = "Call"),
            Citation(5, "conversation", conversationId = 5),
            Citation(6, "web", url = "https://example.com", title = "Page"),
            Citation(7, "message", conversationId = 8),
        )
        val n = ChatCommands.answerNote(md, cites, "From Ambrose")
        assertEquals("Your trip to Chicago", n.title)
        assertTrue(n.body.startsWith("## **Your trip** to [Chicago](https://example.com) [1]"))
        assertEquals(listOf(
            """{"target_kind":"conversation","target_ref":{"conversation_id":5}}""",
            """{"target_kind":"event","target_ref":{"uid":"ev1","occurrence":"2026-10-20"}}""",
            """{"target_kind":"task","target_ref":{"uid":"t1"}}""",
            """{"target_kind":"recording","target_ref":{"connector_id":2,"recording_id":9}}"""), n.links.map { it.toString() })
        // No words on the first line: "From <name>"; a long line is cut at 80.
        assertEquals("From Ambrose", ChatCommands.answerNote("---\n[1](mailr-cite:1)", emptyList(), "From Ambrose").title)
        assertEquals("a".repeat(79) + "…", ChatCommands.answerTitle("a".repeat(100)))
    }

    // The picker's lists stay current.

    @Test fun theSkillsAreReadAgainWhenThePickerOpensAtMostEvery30sAndAfterASkillIsMade() {
        val vm = model()
        fun reads() = requests.count { it.startsWith("GET /api/v1/assistant/skills") }
        assertEquals(1, reads())
        // Opened again at once: the lists are fresh.
        vm.loadCommands()
        idle()
        assertEquals(1, reads())
        try {
            skillsJson = """{"items":[{"name":"formal","title":"Formal","description":"","enabled":true,"auto":false},""" +
                """{"name":"standup","title":"Standup","description":"A daily standup note","enabled":true,"auto":false}]}"""
            io.github.murtazanasir.mailr.time.AppClock.pin(java.time.Instant.ofEpochMilli(io.github.murtazanasir.mailr.time.AppClock.millis() + 31_000))
            vm.loadCommands()
            // The list on screen stays while it reloads.
            assertEquals(listOf("formal"), vm.state.value.skills.map { it.name })
            until("read again") { reads() == 2 && vm.state.value.skills.map { it.name } == listOf("formal", "standup") }
            vm.loadCommands()
            idle()
            assertEquals(2, reads())
            // A skill made in the chat: read again at once, within the 30 s.
            makesSkill = true
            vm.send("Make a standup skill")
            until("read after the skill") { !vm.state.value.busy && reads() == 3 }
            vm.loadCommands()
            idle()
            assertEquals(3, reads())
        } finally {
            io.github.murtazanasir.mailr.time.AppClock.reset()
        }
    }

    @Test fun deleteStartsANewChatAndDeletesWhenTheSnackbarCloses() {
        val vm = answered()
        val seen = Collections.synchronizedList(mutableListOf<ChatsMessage>())
        graphs.scope().launch { vm.renames.collect { seen += it } }
        vm.send("/delete")
        assertTrue(vm.state.value.turns.isEmpty())
        until("the snackbar") { seen.isNotEmpty() }
        assertEquals("Chat deleted.", seen[0].text)
        assertEquals("c9", seen[0].undo?.id)
        assertFalse(requests.any { it.startsWith("DELETE") })
        vm.commitDelete(seen[0].undo!!)
        until("deleted") { requests.any { it.startsWith("DELETE /api/v1/agent/sessions/c9") } }
    }

    @Test fun chatsOpensChatsWithTheWords() {
        val vm = model()
        val seen = Collections.synchronizedList(mutableListOf<CommandEffect>())
        graphs.scope().launch { vm.commandEffects.collect { seen += it } }
        vm.send("/chats invoice")
        until("chats") { seen.isNotEmpty() }
        assertEquals(CommandEffect.OpenChats("invoice"), seen[0])
        assertTrue(vm.state.value.turns.isEmpty())
    }

    // The picker on the screen.

    private val sent = Collections.synchronizedList(mutableListOf<String>())
    private lateinit var back: OnBackPressedDispatcher

    private fun show(state: AssistantState) {
        compose.setContent {
            back = LocalOnBackPressedDispatcherOwner.current!!.onBackPressedDispatcher
            Surface(Modifier.fillMaxSize()) {
                AssistantScreen(state, AssistantCallbacks({ sent += it }, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {}))
            }
        }
        compose.waitForIdle()
    }

    private val field get() = compose.onNode(hasSetTextAction())
    private fun noPicker() = assertTrue(compose.onAllNodes(hasTestTag("command-picker")).fetchSemanticsNodes().isEmpty())

    @Test fun typingASlashOpensThePickerAndATapInsertsTheName() {
        show(AssistantState(commands = COMMANDS, skills = SKILLS))
        noPicker()
        field.performTextInput("/re")
        compose.onNodeWithTag("command-picker").assertIsDisplayed()
        compose.onNodeWithText("Rename this chat").assertIsDisplayed()
        compose.onNodeWithText("/retro").assertIsDisplayed()
        compose.onNodeWithTag("pick-rename").performClick()
        compose.onNodeWithText("/rename ").assertIsDisplayed()
        noPicker()
        assertTrue(sent.isEmpty())
    }

    @Test fun aTapOnANoTextCommandTypedInFullSendsIt() {
        show(AssistantState(commands = COMMANDS, skills = SKILLS))
        field.performTextInput("/new")
        compose.onNodeWithTag("pick-new").performClick()
        compose.waitForIdle()
        assertEquals(listOf("/new"), sent.toList())
    }

    @Test fun sendWhileThePickerIsOpenTakesItsFirstRow() {
        show(AssistantState(commands = COMMANDS, skills = SKILLS))
        field.performTextInput("/ta")
        field.performImeAction()
        compose.onNodeWithText("/task ").assertIsDisplayed()
        assertTrue(sent.isEmpty())
    }

    @Test fun backOrDeletingTheSlashClosesThePicker() {
        show(AssistantState(commands = COMMANDS, skills = SKILLS))
        field.performTextInput("/")
        compose.onNodeWithTag("command-picker").assertIsDisplayed()
        compose.runOnIdle { back.onBackPressed() }
        noPicker()
        // Typed again after the `/` was deleted, it opens again.
        field.performTextClearance()
        field.performTextInput("/h")
        compose.onNodeWithTag("pick-help").assertIsDisplayed()
        field.performTextClearance()
        noPicker()
    }

    @Test fun theLocalOnlyChipShowsWhileTheChatIsLocal() {
        show(AssistantState(localOnly = true))
        compose.onNodeWithTag("chat-local-only").assertIsDisplayed()
        compose.onNodeWithText("Local only").assertIsDisplayed()
    }
}
