package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.testing.until
import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Sort
import androidx.compose.material.icons.outlined.ArrowDropDown
import androidx.compose.material.icons.outlined.CalendarViewMonth
import androidx.compose.material.icons.outlined.CalendarViewWeek
import androidx.compose.material.icons.outlined.Menu
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Today
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.material3.adaptive.layout.AnimatedPane
import androidx.compose.material3.adaptive.layout.ListDetailPaneScaffold
import androidx.compose.material3.adaptive.layout.ListDetailPaneScaffoldRole
import androidx.compose.material3.adaptive.navigation.rememberListDetailPaneScaffoldNavigator
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.fromHtml
import androidx.compose.ui.unit.dp
import androidx.paging.PagingData
import androidx.paging.compose.collectAsLazyPagingItems
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.ApprovalPreview
import io.github.murtazanasir.mailr.data.api.AssistantRun
import io.github.murtazanasir.mailr.data.api.Attachment
import io.github.murtazanasir.mailr.data.api.AttachmentPreview
import io.github.murtazanasir.mailr.data.api.Attendee
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.api.ConversationDetail
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.Identity
import io.github.murtazanasir.mailr.data.api.InboxLayout
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.LayoutTab
import io.github.murtazanasir.mailr.data.api.Message
import io.github.murtazanasir.mailr.data.api.MessageBody
import io.github.murtazanasir.mailr.data.api.Participant
import io.github.murtazanasir.mailr.data.api.PrimaryAction
import io.github.murtazanasir.mailr.data.api.Signal
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.data.reminders.Kind
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.data.voice.VoiceSource
import io.github.murtazanasir.mailr.data.voice.VoiceState
import io.github.murtazanasir.mailr.ui.AppNavigation
import io.github.murtazanasir.mailr.ui.Tab
import io.github.murtazanasir.mailr.ui.approvals.ApprovalRecord
import io.github.murtazanasir.mailr.ui.approvals.ApprovalsScreen
import io.github.murtazanasir.mailr.ui.ask.AskCallbacks
import io.github.murtazanasir.mailr.ui.ask.AskOverlay
import io.github.murtazanasir.mailr.ui.ask.AskUi
import io.github.murtazanasir.mailr.ui.assistant.AssistantCallbacks
import io.github.murtazanasir.mailr.ui.assistant.AssistantScreen
import io.github.murtazanasir.mailr.ui.assistant.AssistantState
import io.github.murtazanasir.mailr.ui.assistant.ChatTurn
import io.github.murtazanasir.mailr.ui.assistant.Step
import io.github.murtazanasir.mailr.ui.assistant.TurnItem
import io.github.murtazanasir.mailr.ui.calendar.AlertCallbacks
import io.github.murtazanasir.mailr.ui.calendar.QuickActionCard
import io.github.murtazanasir.mailr.ui.calendar.QuickActions
import io.github.murtazanasir.mailr.ui.calendar.ReminderAlertScreen
import io.github.murtazanasir.mailr.ui.calendar.SheetTarget
import io.github.murtazanasir.mailr.ui.calendar.TimeGrid
import io.github.murtazanasir.mailr.ui.calendar.dayEvents
import io.github.murtazanasir.mailr.ui.calendar.weekStart
import io.github.murtazanasir.mailr.ui.compose.ComposeBody
import io.github.murtazanasir.mailr.ui.compose.ComposeCallbacks
import io.github.murtazanasir.mailr.ui.compose.ComposeScreen
import io.github.murtazanasir.mailr.ui.compose.ComposeState
import io.github.murtazanasir.mailr.ui.conversation.BodyState
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.conversation.LocalBodyRenderer
import io.github.murtazanasir.mailr.ui.mail.DrawerFavorites
import io.github.murtazanasir.mailr.ui.mail.InboxTabs
import io.github.murtazanasir.mailr.ui.mail.ListActions
import io.github.murtazanasir.mailr.ui.mail.MailDrawer
import io.github.murtazanasir.mailr.ui.mail.MailListPane
import io.github.murtazanasir.mailr.ui.mail.Section
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingCallbacks
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingScreen
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingState
import io.github.murtazanasir.mailr.ui.settings.SettingsCallbacks
import io.github.murtazanasir.mailr.ui.settings.SettingsScreen
import io.github.murtazanasir.mailr.ui.tasks.GroupOptions
import io.github.murtazanasir.mailr.ui.tasks.QuickPrefill
import io.github.murtazanasir.mailr.ui.tasks.RowStyle
import io.github.murtazanasir.mailr.ui.tasks.SwipeAction
import io.github.murtazanasir.mailr.ui.tasks.TaskGroupList
import io.github.murtazanasir.mailr.ui.tasks.TaskGroups
import io.github.murtazanasir.mailr.ui.tasks.TaskListActions
import io.github.murtazanasir.mailr.ui.tasks.TaskQuickAddBar
import io.github.murtazanasir.mailr.ui.tasks.TaskView
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import com.github.takahirom.roborazzi.captureScreenRoboImage
import kotlinx.coroutines.flow.flowOf
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.TimeZone

/**
 * The Android screenshots of the public docs (site/shots.yaml, platform: android), one test per
 * shot, on the demo world of backend/mailr/dev/demo_content.py: Alex Rivera, design lead at
 * Northwind Studio. No real names, addresses or hosts. Output: app/screenshots/docs/<shot>.png,
 * copied into site/docs/assets/shots for the docs.
 *
 * Times: the app reads "now" from AppClock, which the test JVMs pin to the web shots' demo time,
 * Fri 2 Oct 2026 10:40 in America/Chicago (app/build.gradle.kts), and the fixture is built around
 * that. Screens that take "now" as a parameter (the full-screen alert) are pinned to 2026-10-02 13:55.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
@OptIn(ExperimentalMaterial3Api::class)
open class DocsShotsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    companion object {
        /** The demo's zone (Alex Rivera's), the same as the web shots' browser time zone. */
        val zone: ZoneId = ZoneId.of("America/Chicago")
    }

    private val zone: ZoneId get() = DocsShotsTest.zone

    @Before fun setUp() {
        TimeZone.setDefault(TimeZone.getTimeZone(zone))
    }

    private val phone = "w411dp-h891dp-port"
    private val foldOpen = "w841dp-h701dp-land"

    /** Where a shot goes and how it is kept; the Material shots (MaterialLookShotsTest) put theirs elsewhere. */
    protected open fun shotFile(name: String, dark: Boolean): Pair<String, com.github.takahirom.roborazzi.RoborazziOptions> =
        "screenshots/docs/$name.png" to DOCS_SHOT

    /** A shot's scheme; the Material shots take each in both. */
    protected open fun shotDark(dark: Boolean): Boolean = dark

    /** The web's look (the app's default); the Material shots turn it off. */
    protected open val webLook: Boolean get() = true

    /**
     * [then] runs after the first frame (a tap that opens a menu); the shot then takes the whole screen, popups
     * included, unless [popups] is false (a tap that changes the screen itself: the shot takes the content only).
     */
    protected fun shot(name: String, dark: Boolean = false, size: String = phone, then: (() -> Unit)? = null,
                       popups: Boolean = true, content: @Composable () -> Unit) {
        @Suppress("NAME_SHADOWING") val dark = shotDark(dark)
        val (file, options) = shotFile(name, dark)
        RuntimeEnvironment.setQualifiers("$size-${if (dark) "night" else "notnight"}-xxhdpi")
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false, webLook = webLook) {
                CompositionLocalProvider(LocalBodyRenderer provides { html, modifier -> TextBody(html, modifier) }) {
                    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) { content() }
                }
            }
        }
        compose.mainClock.advanceTimeBy(1000)
        compose.waitForIdle()
        if (then != null) {
            then()
            compose.mainClock.advanceTimeBy(1000)
            compose.waitForIdle()
            if (popups) {
                captureScreenRoboImage(file, roborazziOptions = options)
                return
            }
        }
        compose.onRoot().captureRoboImage(file, roborazziOptions = options)
    }

    /** Robolectric draws no WebView: the body as styled text. */
    @Composable
    private fun TextBody(html: String, modifier: Modifier) {
        // fromHtml runs paragraphs together: keep a blank line between them.
        Text(AnnotatedString.fromHtml(html.replace("</p><p>", "<br><br>")), modifier.fillMaxWidth().padding(vertical = 8.dp),
            style = MaterialTheme.typography.bodyLarge)
    }

    // ------------------------------------------------------------------ the demo world

    private val now: ZonedDateTime = AppClock.now(zone).withSecond(0).withNano(0)
    internal val nowMs = now.toInstant().toEpochMilli()
    internal val today: LocalDate = now.toLocalDate()
    private fun ago(minutes: Long) = now.minusMinutes(minutes).toOffsetDateTime().toString()
    private fun at(days: Long, h: Int, m: Int = 0) = now.plusDays(days).withHour(h).withMinute(m).toOffsetDateTime().toString()

    private val myName = "Alex Rivera"
    private val myEmail = "alex@northwind.example"
    private val server = "https://mail.example.com"
    private val me = Participant(myEmail, myName, me = true)
    private val dana = Participant("dana@lumencoffee.example", "Dana Whitfield")
    private val priya = Participant("priya.shah@northwind.example", "Priya Shah")
    private val jordan = Participant("jordan.lee@northwind.example", "Jordan Lee")
    private val sam = Participant("sam.okafor@northwind.example", "Sam Okafor")
    private val mei = Participant("mei.tanaka@northwind.example", "Mei Tanaka")
    private val marco = Participant("marco.silva@bluefin.example", "Marco Silva")
    private val lucia = Participant("lucia@casaverde.example", "Lucía Romero")
    private val theo = Participant("theo.martin@example.org", "Theo Martin")
    private val forgehub = Participant("notifications@forgehub.example", "Forgehub")

    private val labels = listOf(
        Label(1, "Inbox", "inbox", unread = 9, total = 48),
        Label(2, "Sent", "sent"), Label(3, "Drafts", "drafts", total = 2), Label(4, "Spam", "junk", unread = 3),
        Label(5, "Trash", "trash"), Label(6, "Archive", "archive"),
        Label(10, "Clients", color = "#0f9d8a"),
        Label(11, "Clients/Lumen Coffee", color = "#e37400", unread = 1),
        Label(12, "Clients/Bluefin Outdoor", color = "#1a73e8", unread = 1),
        Label(13, "Clients/Casa Verde", color = "#188038", unread = 1),
        Label(14, "Finance", color = "#34a853"),
        Label(15, "Finance/Bills", color = "#d93025", unread = 1),
        Label(16, "Finance/Receipts", color = "#81c995"),
        Label(17, "Dev", color = "#5c6bc0"),
        Label(18, "Dev/Forgehub", color = "#3f51b5", unread = 2),
        Label(19, "Reading", color = "#f9ab00"),
        Label(20, "Studio", color = "#8e24aa"),
        Label(21, "Travel", color = "#a142f4"),
        Label(22, "Personal", color = "#e91e63"),
        Label(23, "Clinic board", color = "#78909c"),
    )

    private val layout = InboxLayout("studio", "Studio", tabs = listOf(
        LayoutTab("inbox", "Inbox", "inbox", indicators = listOf("unread", "due")),
        LayoutTab("code", "Code", "laptop", indicators = listOf("unread")),
        LayoutTab("money", "Bills", "flag", indicators = listOf("due", "all")),
        LayoutTab("updates", "Updates", "bell", indicators = listOf("unread")),
        LayoutTab("reading", "Reading", "book", indicators = listOf("all")),
    ))
    private val tabCounts = mapOf("code" to 2, "money" to 1, "updates" to 4, "reading" to 3)
    private val inboxView = MailView("inbox", title = "Inbox", layout = "studio", tab = "inbox")

    private val lumen = ConversationSummary(3101, "Re: Lumen Coffee: rebrand brief",
        "Quick answers and one ask: the van wrap is in scope, but it can be the last deliverable. Our updated packaging guidelines…",
        ago(25), 3, 1, important = true, starred = true, participants = listOf(dana, me, jordan), labelIds = listOf(1, 11),
        attachmentsPreview = listOf(AttachmentPreview("Lumen-packaging-guidelines.pdf", "application/pdf")),
        signals = listOf(Signal("needs_reply", "Asks you to confirm the Thursday 2 PM review")))
    private val priyaReview = ConversationSummary(3102, "Thursday studio review",
        "Could you present the Lumen concepts at Thursday's studio review? 20 minutes is plenty.",
        ago(70), 1, 1, important = true, participants = listOf(priya), labelIds = listOf(1, 20),
        signals = listOf(Signal("needs_reply", "Asks you to present the Lumen concepts")))
    private val timeline = ConversationSummary(3103, "Bluefin timeline: sign-off needed",
        "Attached is the revised Bluefin timeline. The big change is that content entry moves a week earlier…",
        ago(125), 1, 1, important = true, participants = listOf(jordan), labelIds = listOf(1, 12),
        attachmentsPreview = listOf(AttachmentPreview("Bluefin-timeline-v4.pdf", "application/pdf")),
        signals = listOf(Signal("deadline", date = today.plusDays(2).toString())))
    private val luciaMail = ConversationSummary(3104, "Propuesta para el nuevo catálogo",
        "Hola Alex, gracias por la llamada de ayer. Te escribo para confirmar los próximos pasos del nuevo catálogo.",
        ago(180), 1, 1, participants = listOf(lucia), labelIds = listOf(1, 13))
    private val pr142 = ConversationSummary(3105, "[northwind/website] Add pricing page (PR #142)",
        "Sam Okafor requested your review on this pull request.", ago(240), 2, 2,
        participants = listOf(forgehub), labelIds = listOf(1, 18), category = "updates")
    private val energy = ConversationSummary(3106, "Your studio energy bill is ready",
        "Account 4471-209-88 · Amount due $214.60 · Due in 12 days", ago(540), 1, 0, important = true,
        participants = listOf(Participant("billing@brightline-energy.example", "Brightline Energy")), labelIds = listOf(1, 15),
        primaryAction = PrimaryAction("pay", "Pay bill", "https://brightline-energy.example/pay", fact = "$214.60 due"))
    private val heroes = ConversationSummary(3107, "Homepage hero options for Bluefin",
        "Two hero directions for the Bluefin homepage. Option A leads with the trail photography, option B…",
        ago(26 * 60), 1, 0, participants = listOf(mei), labelIds = listOf(1, 12),
        attachmentsPreview = listOf(AttachmentPreview("hero-a.png", "image/png"), AttachmentPreview("hero-b.png", "image/png")))
    private val quote = ConversationSummary(3108, "Quote for the Bluefin landing page",
        "As discussed, here is our quote for the Bluefin landing page: design, build and two rounds of revisions…",
        ago(3 * 24 * 60 + 240), 1, 0, participants = listOf(me, marco), labelIds = listOf(1, 12),
        attachmentsPreview = listOf(AttachmentPreview("Northwind-quote-Bluefin-landing.pdf", "application/pdf")))
    private val climbing = ConversationSummary(3109, "Climbing Saturday?",
        "Granite Works at 10? I can bring the spare rope.", ago(2 * 24 * 60), 2, 0,
        participants = listOf(theo, me), labelIds = listOf(1))

    private val inbox = listOf(lumen, priyaReview, timeline, luciaMail, pr142, energy, heroes, quote, climbing)

    private val noList = ListActions({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> }, {}, { _, _ -> }, {}, {}, {})
    private val noConv = ConversationActions(back = {}, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
        openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {}, mailto = {}, retry = {})

    @Composable
    private fun MailList(items: List<ConversationSummary> = inbox, sections: List<Section> = emptyList(), title: String? = null,
                         activeId: Long? = null, tabs: Boolean = true) {
        val paged = flowOf(PagingData.from(items)).collectAsLazyPagingItems()
        MailListPane(inboxView, paged, sections, title, labels, emptySet(), activeId, Prefs(), myName, myEmail, false, 0,
            SnackbarHostState(), true, noList,
            tabs = if (tabs) ({ InboxTabs(layout, inboxView, tabCounts, onOpen = {}) }) else null)
    }

    private val lumenLatest = "Hi Alex,\n\nQuick answers and one ask:\n\n- The van wrap is in scope, but it can be the last deliverable.\n" +
        "- Our updated packaging guidelines are attached (new bag sizes).\n\nCould you confirm you can share the two directions " +
        "by Thursday, 2 PM? I want to book the board room for the review. If that slot does not work, Thursday at 4 PM is also fine.\n\n" +
        "Thanks!\nDana"

    /** Plain text as <p> paragraphs (line breaks kept), so the text renderer keeps the blank lines. */
    private fun paragraphs(text: String) = text.split("\n\n").joinToString("") { "<p>" + it.replace("\n", "<br>") + "</p>" }

    private val lumenDetail = ConversationDetail(lumen, listOf(
        Message(601, 3101, Address(dana.email, dana.name), listOf(Address(myEmail, myName)), cc = listOf(Address(jordan.email, jordan.name)),
            subject = "Lumen Coffee: rebrand brief", date = ago(6 * 24 * 60 + 180),
            snippet = "Great to meet you and Jordan last week. As promised, our brand brief is attached.", seen = true,
            attachments = listOf(Attachment(2, "Lumen-brand-brief.pdf", "application/pdf", 1_320_000))),
        Message(602, 3101, Address(myEmail, myName), listOf(Address(dana.email, dana.name)), cc = listOf(Address(jordan.email, jordan.name)),
            subject = "Re: Lumen Coffee: rebrand brief", date = ago(6 * 24 * 60 + 60),
            snippet = "Thanks for the brief, it is a great starting point. We will share two directions on Thursday…", seen = true,
            sentByMe = true),
        Message(603, 3101, Address(dana.email, dana.name), listOf(Address(myEmail, myName)), cc = listOf(Address(jordan.email, jordan.name)),
            subject = "Re: Lumen Coffee: rebrand brief", date = ago(25), snippet = "Quick answers and one ask…", seen = false,
            flagged = true, attachments = listOf(Attachment(3, "Lumen-packaging-guidelines.pdf", "application/pdf", 2_480_000))),
    ))

    private val brief = AssistantRun(1, orderName = "Client mail", status = "done", conversationId = 3101,
        output = "Dana asks you to **confirm Thursday at 2 PM** for the concept review [1](mailr-cite:1). " +
            "The van wrap stays in scope as the last deliverable, and new packaging guidelines are attached.",
        citations = listOf(Citation(1, "message", 3101, 603, "Re: Lumen Coffee: rebrand brief", "Dana Whitfield", ago(25),
            "Could you confirm you can share the two directions by Thursday, 2 PM?", true)),
        finishedAt = ago(20))

    @Composable
    private fun Conversation(withBrief: Boolean = true) {
        ConversationScreen(lumenDetail, null, labels, setOf(603L),
            mapOf(603L to BodyState.Ready(MessageBody(html = paragraphs(lumenLatest), source = "text"), false)),
            null, null, false, SnackbarHostState(), noConv, brief = if (withBrief) brief else null)
    }

    // ------------------------------------------------------------------ mail

    @Test fun homeAndroid() = shot("home-android") { AppNavigation(Tab.MAIL, {}, 9) { MailList() } }

    @Test fun inbox() = shot("android-inbox") { AppNavigation(Tab.MAIL, {}, 9) { MailList() } }

    @Test fun onboardingServer() = shot("android-onboarding-server") {
        OnboardingScreen(OnboardingState(server), OnboardingCallbacks({}, {}, {}, {}, { _, _ -> }), io.github.murtazanasir.mailr.ui.onboarding.MailrBrand)
    }

    /** A setup link names a server: the app asks first, with the full address. */
    @Test fun onboardingSetupLink() = shot("android-setup-link") {
        OnboardingScreen(OnboardingState("", setupOffer = server), OnboardingCallbacks({}, {}, {}, {}, { _, _ -> }),
            io.github.murtazanasir.mailr.ui.onboarding.MailrBrand)
    }

    @Test fun listDetailFold() = shot("android-list-detail-fold", size = foldOpen) {
        AppNavigation(Tab.MAIL, {}, 9) {
            val nav = rememberListDetailPaneScaffoldNavigator<String>()
            LaunchedEffect(Unit) { nav.navigateTo(ListDetailPaneScaffoldRole.Detail, "3101") }
            ListDetailPaneScaffold(nav.scaffoldDirective, nav.scaffoldValue,
                listPane = { AnimatedPane { MailList(activeId = 3101) } },
                detailPane = { AnimatedPane { Conversation() } })
        }
    }

    @Test fun priorityInbox() = shot("android-priority-inbox") {
        AppNavigation(Tab.MAIL, {}, 9) {
            MailList(items = listOf(pr142, energy, heroes, quote, climbing),
                sections = listOf(Section("now", "Important and unread", listOf(lumen, priyaReview, timeline)),
                    Section("clients", "Clients", listOf(luciaMail))),
                title = "Everything else")
        }
    }

    @Test fun drawer() = shot("android-drawer") {
        MailDrawer(labels, inboxView, layout, tabCounts, myEmail, 1, {}, {}, {},
            favorites = DrawerFavorites(listOf("inbox", "starred", "label:11", "label:15")))
    }

    @Test fun conversation() = shot("android-conversation", dark = true) { Conversation() }

    /** An invitation with its card (a clash, Yes · Maybe · No) and the suggested replies. */
    @Test fun inviteCard() = shot("android-invite-card") {
        val m = Message(701, 3201, Address(dana.email, dana.name), listOf(Address(myEmail, myName)),
            subject = "Invitation: Lumen kickoff", date = ago(40), snippet = "Kickoff with the Lumen team", seen = true,
            attachments = listOf(Attachment(2, "invite.ics", "text/calendar", 1_240)))
        val d = ConversationDetail(lumen.copy(id = 3201, subject = "Invitation: Lumen kickoff"), listOf(m))
        val inv = io.github.murtazanasir.mailr.data.api.Invite(method = "REQUEST", uid = "lumen-kickoff@lumencoffee.example", summary = "Lumen kickoff",
            start = at(2, 10), end = at(2, 11), location = "Lumen Coffee, 4th floor",
            organizer = io.github.murtazanasir.mailr.data.api.InviteOrganizer(dana.email, dana.name.orEmpty()),
            attendees = listOf(io.github.murtazanasir.mailr.data.api.InviteAttendee(myEmail, myName), io.github.murtazanasir.mailr.data.api.InviteAttendee(priya.email, priya.name.orEmpty())),
            myStatus = "NEEDS-ACTION", conflicts = listOf(io.github.murtazanasir.mailr.data.api.InviteConflict("Design review")))
        ConversationScreen(d, null, labels, setOf(701L),
            mapOf(701L to BodyState.Ready(MessageBody(html = paragraphs("Hi Alex,\n\nHere is the invitation for our kickoff. Priya will join too.\n\nDana"), source = "text"), false)),
            null, null, false, SnackbarHostState(), noConv,
            extras = io.github.murtazanasir.mailr.ui.conversation.ReaderExtrasUi(invites = mapOf(701L to inv),
                replies = 701L to listOf("I'll be there.", "Can we start at 10:30?", "Thanks, Dana!")))
    }

    /** event.change_suggestion on android.reader: a move announced in words, as one card with Move and Not now. */
    @Test fun moveSuggestion() = shot("android-move-suggestion") {
        val m = Message(721, 3202, Address(dana.email, dana.name), listOf(Address(myEmail, myName)),
            subject = "Re: Lumen kickoff", date = ago(35), snippet = "Could we move our kickoff to Wednesday at 10?", seen = true,
            eventChange = io.github.murtazanasir.mailr.data.api.EventChange(uid = "lumen-kickoff@lumencoffee.example", summary = "Lumen kickoff",
                oldStart = at(4, 10), oldEnd = at(4, 11), newStart = at(5, 10), newEnd = at(5, 11),
                sentence = "Could we move our kickoff to Wednesday at 10?"))
        val d = ConversationDetail(lumen.copy(id = 3202, subject = "Re: Lumen kickoff"), listOf(m))
        ConversationScreen(d, null, labels, setOf(721L),
            mapOf(721L to BodyState.Ready(MessageBody(html = paragraphs("Hi Alex,\n\nSomething came up on Tuesday morning. " +
                "Could we move our kickoff to Wednesday at 10? Same room.\n\nDana"), source = "text"), false)),
            null, null, false, SnackbarHostState(), noConv)
    }

    /** mail.open_attached_email on android.reader: a message forwarded as an attachment, its card open. */
    @Test fun attachedEmail() = shot("android-attached-email") {
        val summary = io.github.murtazanasir.mailr.data.api.AttachedEmailSummary(subject = "New access cards from Monday",
            fromName = "Harbor Property Management", fromEmail = "office@harborproperty.example", date = ago(60 * 24 * 20 + 120),
            excerpt = "The building moves to new access cards on Monday.")
        val m = Message(721, 3221, Address("ops@northwind.example", "Northwind Ops"), listOf(Address("team@northwind.example")),
            subject = "Fwd: New access cards from Monday", date = ago(60 * 24 * 20), snippet = "", seen = true,
            attachments = listOf(Attachment(2, "New access cards from Monday.eml", "message/rfc822", 1_480, email = summary)))
        val d = ConversationDetail(lumen.copy(id = 3221, subject = "Fwd: New access cards from Monday", starred = false,
            important = false, labelIds = listOf(1), signals = emptyList(), attachmentsPreview = emptyList()), listOf(m))
        val inner = io.github.murtazanasir.mailr.data.api.AttachedEmailBody(part = 2, subject = summary.subject,
            from = Address(summary.fromEmail, summary.fromName), to = listOf(Address("ops@northwind.example")),
            html = paragraphs("Hello Northwind team,\n\nThe building moves to new access cards on Monday. Old cards stop working " +
                "at 6 PM on Friday.\n\nPlease collect your new card from the front desk on the ground floor, between 8 AM and 5 PM. " +
                "Bring a photo ID.\n\nThank you,\nHarbor Property Management"), source = "text")
        ConversationScreen(d, null, labels, setOf(721L),
            mapOf(721L to BodyState.Ready(MessageBody(html = "", source = "text"), false)),
            null, null, false, SnackbarHostState(), noConv,
            extras = io.github.murtazanasir.mailr.ui.conversation.ReaderExtrasUi(attached = mapOf(
                io.github.murtazanasir.mailr.ui.conversation.attachedKey(721L, 2) to io.github.murtazanasir.mailr.ui.conversation.AttachedState.Ready(inner, false))))
    }

    /** mail.translate on android.reader: a message from Lucía translated from Spanish, with Show original. */
    @Test fun translate() = shot("android-translate") {
        val m = Message(711, 3211, Address(lucia.email, lucia.name), listOf(Address(myEmail, myName)),
            subject = "Fotos para la carta de otoño", date = ago(95), snippet = "Hola Alex, te mando las fotos…", seen = true,
            offerTranslate = true)
        val d = ConversationDetail(lumen.copy(id = 3211, subject = "Fotos para la carta de otoño"), listOf(m))
        val r = io.github.murtazanasir.mailr.ui.conversation.TranslationResult(source = "Spanish", to = "English",
            text = "Hi Alex,\n\nHere are the photos for the autumn menu. Could you use the second one for the cover? " +
                "We would like to print the menus by Friday.\n\nThank you,\nLucía")
        ConversationScreen(d, null, labels, setOf(711L),
            mapOf(711L to BodyState.Ready(MessageBody(html = paragraphs("Hola Alex,\n\nTe mando las fotos para la carta de otoño."), source = "text"), false)),
            null, null, false, SnackbarHostState(), noConv.copy(translate = { _, _ -> }),
            extras = io.github.murtazanasir.mailr.ui.conversation.ReaderExtrasUi(translations = mapOf(711L to io.github.murtazanasir.mailr.ui.conversation.Translation.Ready(r))))
    }

    /** The inbox of a member whose mail account is not connected yet. */
    @Test fun finishOnWeb() = shot("android-finish-on-web") {
        AppNavigation(Tab.MAIL, {}, 0) {
            io.github.murtazanasir.mailr.ui.onboarding.FinishOnWebState(io.github.murtazanasir.mailr.data.api.SetupState(false, "pending", true, "$server/setup")) {}
        }
    }

    @Test fun compose() = shot("android-compose") {
        ComposeScreen(
            ComposeState(loading = false, identities = listOf(Identity(1, myEmail, myName, isDefault = true)),
                identityId = 1, to = listOf(Address(dana.email, dana.name)), cc = listOf(Address(jordan.email, jordan.name)),
                showCc = true, subject = "Re: Lumen Coffee: rebrand brief",
                blocks = ComposeBody.ofText("Hi Dana,\n\nThursday at 2 PM works. We will bring both directions and the " +
                    "packaging mockups, and the van wrap can follow last.\n\nSee you then,\nAlex"),
                quoted = "<div class=\"mailr_quote\">On Thu, Dana Whitfield wrote: …</div>",
                title = "Reply", saveStatus = "Saved"),
            SnackbarHostState(),
            ComposeCallbacks({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> true }, { _, _ -> }, { _, _ -> }, { _, _ -> }, {}, {}, {}),
        )
    }

    // ------------------------------------------------------------------ calendar, tasks, reminders

    internal val cals = listOf(
        CalendarCollection("work", "Work", "#2563eb", calendarId = 1),
        CalendarCollection("personal", "Personal", "#16a34a", calendarId = 2),
        CalendarCollection("clinic", "Clinic board", "#64748b", calendarId = 3),
    )
    private val week = weekStart(today)
    private val monday = week.with(java.time.temporal.TemporalAdjusters.nextOrSame(java.time.DayOfWeek.MONDAY))
    private fun on(d: LocalDate, h: Int, m: Int = 0) = d.atTime(h, m).atZone(zone).toOffsetDateTime().toString()

    /** The demo's "today" events go on a weekday, also when the shots are recorded on a weekend. */
    private val workday = if (today.dayOfWeek.value >= 6) monday.plusDays(4) else today

    internal val events: List<CalendarEvent> = buildList {
        (0L until 5L).forEach { i ->
            val d = monday.plusDays(i)
            add(CalendarEvent("standup-$i", "Studio standup", on(d, 9, 30), on(d, 9, 45), calendar = "work", recurring = true,
                location = "meet.northwind.example/standup"))
        }
        val tue = monday.plusDays(1); val wed = monday.plusDays(2); val thu = monday.plusDays(3)
        add(CalendarEvent("one-on-one", "1:1 with Priya", on(tue, 11), on(tue, 11, 30), calendar = "work", recurring = true,
            organizer = priya.email, organizerName = priya.name))
        add(CalendarEvent("planning", "Sprint planning", on(monday, 14), on(monday, 15), calendar = "work", location = "Studio, big room"))
        add(CalendarEvent("content", "Bluefin content review", on(wed, 13), on(wed, 14), calendar = "work",
            organizer = jordan.email, organizerName = jordan.name))
        add(CalendarEvent("lumen-review", "Lumen concept review", on(thu, 14), on(thu, 15), calendar = "work",
            location = "Lumen Coffee Roasters, board room"))
        add(CalendarEvent("studio-review", "Studio review", on(thu, 16, 30), on(thu, 17, 30), calendar = "work"))
        add(CalendarEvent("moodboard", "Lumen moodboard session", on(workday, 10, 30), on(workday, 12), calendar = "work",
            location = "Studio, room 2"))
        add(CalendarEvent("lunch-mei", "Lunch with Mei", on(workday, 12, 15), on(workday, 13), calendar = "work", status = "TENTATIVE",
            myStatus = "TENTATIVE"))
        add(CalendarEvent("casa", "Call with Casa Verde", on(workday, 15), on(workday, 15, 30), calendar = "work"))
        val sat = monday.plusDays(5)
        add(CalendarEvent("climb", "Climbing with Theo", on(sat, 10), on(sat, 12), calendar = "personal"))
        add(CalendarEvent("fair", "Health fair (volunteer)", on(sat, 8, 30), on(sat, 13), calendar = "clinic"))
        add(CalendarEvent("sprint", "Lumen design sprint", workday.minusDays(2).toString(), workday.plusDays(1).toString(),
            allDay = true, calendar = "work"))
    }

    @Test fun calendarWeek() = shot("android-calendar-week") {
        AppNavigation(Tab.CALENDAR, {}, 9) {
            Column {
                TopAppBar(navigationIcon = { IconButton(onClick = {}) { Icon(Icons.Outlined.Menu, "Calendars") } },
                    title = {
                        androidx.compose.foundation.layout.Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(today.format(java.time.format.DateTimeFormatter.ofPattern("MMMM yyyy")))
                            Icon(Icons.Outlined.ArrowDropDown, "Pick a month")
                        }
                    },
                    actions = {
                        IconButton(onClick = {}) { Icon(Icons.Outlined.Search, "Search events") }
                        IconButton(onClick = {}) { Icon(Icons.Outlined.Today, "Today") }
                        IconButton(onClick = {}) { Icon(Icons.Outlined.CalendarViewWeek, "View: Week") }
                        IconButton(onClick = {}) { Icon(Icons.Outlined.MoreVert, "More") }
                    })
                TimeGrid((0L until 7L).map { week.plusDays(it) }, dayEvents(events, cals, zone), nowMs, null, {}, {}, {}, zone)
            }
        }
    }

    /** The month on a phone (note #14): titles in the days, the picked day's dense agenda below. */
    @Test fun calendarMonth() = shot("android-calendar-month") {
        val first = today.withDayOfMonth(1)
        val month = events + (0L until 31L).map { first.plusDays(it) }.filter { it.month == first.month && it.dayOfWeek.value <= 5 &&
            (it.isBefore(monday) || it.isAfter(monday.plusDays(4))) }.flatMap { d ->
            listOfNotNull(CalendarEvent("su-$d", "Studio standup", on(d, 9, 30), on(d, 9, 45), calendar = "work", recurring = true),
                if (d.dayOfWeek.value == 3) CalendarEvent("rev-$d", "Client review", on(d, 14), on(d, 15), calendar = "work") else null,
                if (d.dayOfWeek.value == 5) CalendarEvent("run-$d", "Run club", on(d, 18), on(d, 19), calendar = "personal") else null)
        }
        val evs = dayEvents(month, cals, zone)
        AppNavigation(Tab.CALENDAR, {}, 9) {
            Column {
                TopAppBar(navigationIcon = { IconButton(onClick = {}) { Icon(Icons.Outlined.Menu, "Calendars") } },
                    title = {
                        androidx.compose.foundation.layout.Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(today.format(java.time.format.DateTimeFormatter.ofPattern("MMMM")))
                            Icon(Icons.Outlined.ArrowDropDown, "Pick a month")
                        }
                    },
                    actions = {
                        IconButton(onClick = {}) { Icon(Icons.Outlined.Search, "Search events") }
                        IconButton(onClick = {}) { Icon(Icons.Outlined.Today, "Today") }
                        IconButton(onClick = {}) { Icon(Icons.Outlined.CalendarViewMonth, "View: Month") }
                        IconButton(onClick = {}) { Icon(Icons.Outlined.MoreVert, "More") }
                    })
                io.github.murtazanasir.mailr.ui.calendar.MonthView(workday, evs, false, {}, {}, Modifier.weight(0.64f))
                androidx.compose.material3.HorizontalDivider()
                io.github.murtazanasir.mailr.ui.calendar.DayList(workday, evs, null, {}, {}, Modifier.weight(0.36f), compact = true)
            }
        }
    }

    /** The Agenda widget (notes #15 and #16) on a home screen in its default style (week strip), with the 1x1 Talk to Ambrose widget. */
    @Test fun agendaWidget() {
        RuntimeEnvironment.setQualifiers("$phone-notnight-xxhdpi")
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        val morning = workday.atTime(9, 10).atZone(zone)
        val model = io.github.murtazanasir.mailr.ui.agenda.AgendaModel.build(events.map { e -> e.copy(calendarColor = cals.firstOrNull { it.url == e.calendar }?.color) },
            listOf(
                TaskItem("g1", "Renew the studio insurance", workday.minusDays(2).toString(), dueAllDay = true),
                TaskItem("g2", "Send the Lumen invoice", on(workday, 15)),
                TaskItem("g3", "Book the venue for the offsite", workday.plusDays(1).toString(), dueAllDay = true),
                TaskItem("g4", "Return the library books", workday.plusDays(3).toString(), dueAllDay = true),
            ), morning, 5, weather = io.github.murtazanasir.mailr.ui.agenda.AgendaModel.Weather(io.github.murtazanasir.mailr.ui.agenda.WeatherIcon.PARTLY_DAY, 18, 23, 11, null))
        homeScreen(ctx, false, 411, 520, listOf(
            PlacedWidget(16, 24, 379, 380) { io.github.murtazanasir.mailr.ui.agenda.AgendaWidgetContent(model) },
            PlacedWidget(16, 420, 80, 92) { io.github.murtazanasir.mailr.ui.ask.AskWidgetContent(ctx) },
        )).captureRoboImage("screenshots/docs/android-agenda-widget.png", roborazziOptions = DOCS_SHOT)
    }

    /** The Agenda widget's four styles side by side (the mockups' sample day, 14:05 on Wednesday 7 October, weeks from Monday). */
    @Test fun agendaStyles() {
        val old = java.util.Locale.getDefault()
        try {
            java.util.Locale.setDefault(java.util.Locale.UK)
            RuntimeEnvironment.setQualifiers("en-rGB-$phone-notnight-xxhdpi")
            val ctx = ApplicationProvider.getApplicationContext<Application>()
            val m = AgendaShots.model()
            val styles = io.github.murtazanasir.mailr.ui.agenda.AgendaStyle.entries
            homeScreen(ctx, false, 790, 674, styles.mapIndexed { i, st ->
                PlacedWidget(16 + (i % 2) * 385, 16 + (i / 2) * 327, 373, 315) { io.github.murtazanasir.mailr.ui.agenda.AgendaWidgetContent(m, st) }
            }, wallpaper = true).captureRoboImage("screenshots/docs/android-agenda-styles.png", roborazziOptions = DOCS_SHOT)
        } finally {
            java.util.Locale.setDefault(old)
        }
    }

    private val lists = listOf(
        CalendarCollection("studio", "Studio", "#d97706", tasks = true, calendarId = 4),
        CalendarCollection("errands", "Errands", "#7c3aed", tasks = true, calendarId = 5),
    )
    private val tasks = listOf(
        TaskItem("insurance", "Renew the studio insurance", today.minusDays(3).toString(), list = "studio", priority = 1,
            categories = listOf("admin")),
        TaskItem("library", "Return the library books", today.minusDays(1).toString(), list = "errands", categories = listOf("errands")),
        TaskItem("invoice", "Send the Lumen deposit invoice", today.toString(), list = "studio", priority = 1,
            categories = listOf("billing"), conversationId = 3101),
        TaskItem("expenses", "Submit the expense report", at(0, 11), list = "studio", reminders = listOf(0), categories = listOf("admin")),
        TaskItem("deck", "Prepare the Lumen concept deck", at(0, 16), list = "studio", priority = 5, subtaskCount = 3,
            notes = "Two directions, packaging mockups, the van wrap sketch.", categories = listOf("lumen")),
        TaskItem("deck-1", "Export the moodboard", null, list = "studio", parent = "deck", status = "completed"),
        TaskItem("deck-2", "Write the design rationale", today.toString(), list = "studio", parent = "deck"),
        TaskItem("deck-3", "Rehearse with Jordan", null, list = "studio", parent = "deck"),
        TaskItem("timesheet", "Weekly timesheet", at(0, 16, 30), list = "studio", recurring = true, rrule = "FREQ=WEEKLY;BYDAY=FR",
            categories = listOf("admin")),
        TaskItem("climb", "Pack climbing shoes", today.plusDays(1).toString(), list = "errands", categories = listOf("weekend")),
        TaskItem("gift", "Buy a birthday present for Mom", today.plusDays(4).toString(), list = "errands", priority = 1,
            categories = listOf("family", "errands")),
        TaskItem("portfolio", "Update the Bluefin case study", null, start = today.plusDays(7).toString(), list = "studio",
            categories = listOf("portfolio")),
    )
    private val followUp = quote.copy(followUpAt = at(0, 9))
    private val noTasks = TaskListActions({}, {}, {}, { _, _ -> }, {}, { _, _, _ -> }, { _, _ -> }, {}, {}, {})

    @Composable
    private fun TasksToday(text: String = "") {
        val groups = TaskGroups.build(TaskView.TODAY, tasks, nowMs, zone, GroupOptions(lists = lists))
        Column {
            TopAppBar(navigationIcon = { IconButton(onClick = {}) { Icon(Icons.Outlined.Menu, "Lists and tags") } },
                title = {
                    Column {
                        Text("Today")
                        Text("${groups.sumOf { it.count }} tasks", style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                },
                actions = {
                    IconButton(onClick = {}) { Icon(Icons.Outlined.Search, "Search") }
                    IconButton(onClick = {}) { Icon(Icons.AutoMirrored.Outlined.Sort, "Sort") }
                    IconButton(onClick = {}) { Icon(Icons.Outlined.MoreVert, "More") }
                })
            TaskGroupList(groups, nowMs, lists, emptySet(), SwipeAction.COMPLETE to SwipeAction.RESCHEDULE, RowStyle(), noTasks,
                followUps = listOf(followUp), followUpTitle = "Follow-ups", modifier = Modifier.weight(1f))
            TaskQuickAddBar(lists, QuickPrefill.of(TaskView.TODAY, today), { now }, remember { FocusRequester() },
                { _, _ -> }, { _, _ -> }, {}, initialText = text, chipsAbove = true)
        }
    }

    @Test fun tasksToday() = shot("android-tasks-today") {
        AppNavigation(Tab.TASKS, {}, 9) { TasksToday("Call Dana fri 3pm !! #Work @phone every week") }
    }

    private val actions by lazy {
        QuickActions(graphs.graph(), ApplicationProvider.getApplicationContext())
    }

    @Test fun reminderSheet() {
        val start = AppClock.now(zone).plusMinutes(40).withSecond(0).withNano(0)
        val event = CalendarEvent("lumen-review", "Lumen concept review", start.toOffsetDateTime().toString(),
            start.plusHours(1).toOffsetDateTime().toString(), location = "Lumen Coffee Roasters, board room")
        val reminder = ReminderEntity("e|lumen-review|${event.occurrence}|30", Kind.EVENT, "lumen-review", event.occurrence, event.summary, "",
            0, 0, start.toInstant().toEpochMilli(), start.plusHours(1).toInstant().toEpochMilli(), RState.SHOWN, updatedAt = 0)
        shot("android-reminder-sheet") {
            Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.scrim.copy(alpha = 0.3f)) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.BottomCenter) {
                    Surface(shape = MaterialTheme.shapes.extraLarge, color = MaterialTheme.colorScheme.surfaceContainerHigh,
                        modifier = Modifier.fillMaxWidth().padding(12.dp)) {
                        QuickActionCard(SheetTarget(reminder, event = event), actions, 9, "snooze", run = {})
                    }
                }
            }
        }
    }

    private val pinned = ZonedDateTime.of(2026, 10, 2, 13, 55, 0, 0, zone)

    @Test fun fullScreenEvent() = shot("android-fullscreen-event", dark = true) {
        val start = pinned.plusMinutes(5).toInstant().toEpochMilli()
        ReminderAlertScreen(ReminderEntity("e|lumen-review|x|5", Kind.EVENT, "lumen-review", "x", "Lumen concept review",
            "2:00 – 3:00 PM · Lumen Coffee Roasters, board room", start, start, start, start + 3_600_000L, RState.SHOWN, updatedAt = 0),
            "https://meet.northwind.example/lumen-review", pinned.toInstant().toEpochMilli(),
            AlertCallbacks({}, {}, {}, {}, {}, {}, {}), zone)
    }

    // ------------------------------------------------------------------ assistant and sharing

    private val noAssistant = AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {})

    @Test fun assistant() = shot("android-assistant") {
        AppNavigation(Tab.ASSISTANT, {}, 9) {
            AssistantScreen(
                AssistantState(turns = listOf(
                    ChatTurn("user", "What needs a reply from me this week?"),
                    ChatTurn("assistant", items = listOf(
                        TurnItem.Steps(0, listOf(Step("1", "search_conversations", "Searching your inbox", true, count = 40),
                            Step("2", "get_conversation", "Reading “Re: Lumen Coffee: rebrand brief”", true),
                            Step("3", "get_conversation", "Reading “Bluefin timeline: sign-off needed”", true))),
                        TurnItem.Text(1, "", "Three emails are waiting on you:\n\n" +
                            "- **Dana Whitfield** asks you to confirm the Lumen concept review on Thursday at 2 PM [1](mailr-cite:1).\n" +
                            "- **Jordan Lee** needs your sign-off on the Bluefin timeline in two days [2](mailr-cite:2).\n" +
                            "- **Priya Shah** asks whether you can present the Lumen concepts at the studio review [3](mailr-cite:3).\n\n" +
                            "Want me to draft the replies?")),
                        citations = listOf(
                            Citation(1, "message", 3101, 603, "Re: Lumen Coffee: rebrand brief", "Dana Whitfield", ago(25),
                                "Could you confirm you can share the two directions by Thursday, 2 PM?", true),
                            Citation(2, "message", 3103, 604, "Bluefin timeline: sign-off needed", "Jordan Lee", ago(125),
                                "Please review and sign off by Sunday", true),
                            Citation(3, "message", 3102, 605, "Thursday studio review", "Priya Shah", ago(70),
                                "Could you present the Lumen concepts at Thursday's studio review?", true)),
                        startedAt = 0, finishedAt = 9_400),
                )),
                noAssistant,
            )
        }
    }

    /** The chat's slash commands as GET /agent/commands lists them in English (backend agent/commands.py), and two skills. */
    private val docsCommands = listOf(
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("new", listOf("clear"), "app", "chat", summary = "Start a new chat"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("chats", kind = "app", group = "chat", arg = "optional", summary = "Open your chats, or search them"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("rename", kind = "app", group = "chat", arg = "required", summary = "Rename this chat", example = "/rename Fitness log"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("retitle", kind = "app", group = "chat", summary = "Make a new short title for this chat"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("delete", kind = "app", group = "chat", summary = "Delete this chat"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("retry", kind = "app", group = "chat", summary = "Write the last answer again"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("copy", kind = "app", group = "chat", summary = "Copy the last answer"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("note", kind = "app", group = "chat", summary = "Save the last answer to a note"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("local", kind = "app", group = "chat", summary = "Keep this chat on your local model"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("model", kind = "info", group = "chat", summary = "Show which model answers in this chat"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("help", kind = "app", group = "chat", summary = "List the commands and your skills"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("task", kind = "ask", group = "add", arg = "required", summary = "Add a task", example = "/task call the bank fri 3pm"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("event", kind = "ask", group = "add", arg = "required", summary = "Add a calendar event", example = "/event dentist tue 10am for 45 min"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("remember", kind = "ask", group = "add", arg = "required", summary = "Ask Ambrose to remember something", example = "/remember my gym closes at 9pm on Sundays"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("forget", kind = "ask", group = "add", arg = "required", summary = "Ask Ambrose to forget something", example = "/forget my old phone number"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("today", kind = "ask", group = "ask", summary = "Today: calendar, tasks due and mail that needs you"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("week", kind = "ask", group = "ask", summary = "This week: events, deadlines and mail waiting for you"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("summarize", kind = "ask", group = "ask", arg = "optional", summary = "Summarize the open conversation"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("reply", kind = "ask", group = "ask", arg = "optional", summary = "Draft a reply to the open conversation"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("waiting", kind = "ask", group = "ask", summary = "Mail you sent that still waits for an answer"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("find", kind = "ask", group = "ask", arg = "required", summary = "Search your mail", example = "/find invoice from the plumber"),
        io.github.murtazanasir.mailr.ui.assistant.ChatCommand("sweep", listOf("cleanup"), "ask", "ask", "optional", "Review the whole list on screen and propose what to clear out, on one card"),
    )
    private val docsSkills = listOf(
        io.github.murtazanasir.mailr.ui.assistant.SkillLite("client-replies", "Client replies", "Short, warm replies to studio clients"),
        io.github.murtazanasir.mailr.ui.assistant.SkillLite("formal", "Formal", "A formal, polite register"),
    )
    private val docsChat = io.github.murtazanasir.mailr.ui.assistant.ChatsScreenshotTest.chatNoop

    /** agent.commands: "/" typed at the start of a message opens the picker over the message box. */
    @Test fun assistantCommands() = shot("android-assistant-commands") {
        AssistantScreen(AssistantState(turns = listOf(
            ChatTurn("user", "When is Priya free next week?"),
            ChatTurn("assistant", items = listOf(TurnItem.Text(0, "",
                "Priya is free on Tuesday afternoon and Thursday morning. She runs the reading group on Tuesdays at 4.")),
                finishedAt = 1),
        ), sessionId = "c2", title = "When is Priya free next week?", commands = docsCommands, skills = docsSkills), docsChat,
            voice = io.github.murtazanasir.mailr.ui.assistant.ComposerVoice(AskUi(null, text = "/"), {}, {}, {}, {}))
    }

    /**
     * agent.review_mail: the demo's finished /sweep review (backend demo.py _seed_review, the card from
     * agent/review.py card()), its progress line, and Atlas Outfitters unticked under Move to Trash.
     */
    // Atlas Outfitters moved from Move to Trash to Archive in its move menu, as the web shot (assistant-review-card).
    @Test fun reviewCard() = shot("android-review-card", popups = false, then = {
        compose.onAllNodesWithTag("review-move")[1].performClick()
        compose.mainClock.advanceTimeBy(500)
        compose.onNodeWithTag("review-move-archive").performClick()
    }) {
        val card = io.github.murtazanasir.mailr.data.api.MailrJson.decodeFromString(Approval.serializer(), DOCS_REVIEW_CARD)
        AssistantScreen(AssistantState(turns = listOf(
            ChatTurn("user", "/sweep keep offers that are still running"),
            ChatTurn("assistant", items = listOf(
                TurnItem.Steps(0, listOf(Step("1", "review_mail", "Starting a review of the list", true, icon = "label"))),
                TurnItem.Text(1, "", "I'm reviewing your mail from Fieldnote Paper Co. and Atlas Outfitters. " +
                    "The review card will appear in this chat when it is done.")),
                approvals = listOf(card), reviews = listOf(1L), finishedAt = 1),
        ), sessionId = "c8", title = "Clean up the shop mail",
            reviews = mapOf(1L to io.github.murtazanasir.mailr.data.api.ReviewState(1, "done", card.review!!.scope, total = 14, done = 14,
                approvalId = card.id))),
            docsChat.copy(followReview = {}, reviewAction = { _, _ -> }, applyReview = { _, _ -> }, reviewFilter = { _, _, _ -> }))
    }

    private val pending = listOf(
        Approval(51, "assistant", "Ambrose", "mail.send", marco.email,
            ApprovalPreview("Send “Re: Quote for the Bluefin landing page”", listOf("To Marco Silva <${marco.email}>",
                "Hi Marco, following up on the quote from Monday. Happy to walk through it on a call this week. Alex"))),
        Approval(52, "token:3", "Planning agent", "calendar.create", "Work",
            ApprovalPreview("Create event", listOf("Thu, 14:00–15:00", "Lumen concept review")),
            expiresAt = now.plusHours(20).toOffsetDateTime().toString()),
    )
    private val history = listOf(
        ApprovalRecord(50, "Ambrose", "tasks.create", "Studio", ApprovalPreview("Add task", listOf("Prepare the Lumen concept deck")),
            "executed", decidedAt = now.minusMinutes(25).toOffsetDateTime().toString(),
            executedAt = now.minusMinutes(25).toOffsetDateTime().toString()),
        ApprovalRecord(49, "Ambrose", "mail.send", sam.email, ApprovalPreview("Send “Friday lunch”"), "denied",
            decidedAt = now.minusHours(3).toOffsetDateTime().toString()),
        ApprovalRecord(48, "Planning agent", "calendar.update", "Work", ApprovalPreview("Move “Studio standup”"), "failed",
            decidedAt = now.minusDays(1).toOffsetDateTime().toString(),
            result = kotlinx.serialization.json.buildJsonObject {
                put("error", kotlinx.serialization.json.JsonPrimitive("The event was deleted on the server."))
            }),
        ApprovalRecord(47, "Planning agent", "mail.label", "", ApprovalPreview("Label 3 conversations"), "expired",
            decidedAt = now.minusDays(2).toOffsetDateTime().toString()),
    )

    @Test fun approvals() = shot("android-approvals") {
        ApprovalsScreen(pending, history, null, false, SnackbarHostState(), {}, {}, { _, _, _ -> }, onAsk = {})
    }

    /** The web bell's sections on Android: an approval, a message not sent, a reminder now and a missed one. */
    @Test fun waiting() = shot("android-waiting") {
        fun rem(key: String, kind: String, title: String, at: ZonedDateTime, missed: Boolean = false) =
            io.github.murtazanasir.mailr.data.db.ReminderEntity(key, kind, "uid-$key", null, title, "", at.toInstant().toEpochMilli(),
                at.toInstant().toEpochMilli(), at.toInstant().toEpochMilli(), null, "SHOWN", missed = missed, updatedAt = 0)
        ApprovalsScreen(pending.take(1), emptyList(), null, false, SnackbarHostState(), {}, {}, { _, _, _ -> }, onAsk = {},
            notSent = listOf(io.github.murtazanasir.mailr.data.api.OutboxRow(77, "failed", "Re: Lumen Coffee: rebrand brief", listOf(dana.email),
                error = "connection timed out")), onOutbox = {},
            reminders = listOf(rem("e1", "EVENT", "Lumen concept review", now.plusMinutes(10)),
                rem("t1", "TASK", "Renew the studio insurance", now.minusDays(1).withHour(17).withMinute(0), missed = true)),
            onReviewAll = {})
    }

    /** approval.approve, approval.undo_grant: two waiting label creates from one agent; the first card's ⋮ menu (More ways to allow) says what each grant covers. */
    @Test fun approvalGrant() = shot("android-approval-grant", then = {
        compose.onAllNodesWithTag("approval-more")[0].performClick()
    }) {
        val cover = io.github.murtazanasir.mailr.data.api.AlsoCovers(session = 1, always = 1, alwaysDerived = 1)
        ApprovalsScreen(listOf(
            Approval(61, "token:3", "Claude Code", "labels.write", "Receipts", ApprovalPreview("Create label “Receipts”", listOf("Receipts")),
                expiresAt = now.plusHours(23).toOffsetDateTime().toString(), alsoCovers = cover),
            Approval(60, "token:3", "Claude Code", "labels.write", "Travel", ApprovalPreview("Create label “Travel”", listOf("Travel")),
                expiresAt = now.plusHours(23).toOffsetDateTime().toString(), alsoCovers = cover),
        ), emptyList(), null, false, SnackbarHostState(), {}, {}, { _, _, _ -> }, onAsk = {})
    }

    private val noAsk = AskCallbacks({}, {}, {}, {}, {}, {}, {}, { false }, {})
    private val insurance = AskAbout.Task("insurance", "Renew the studio insurance")

    @Test fun askCapture() = shot("android-ask-capture") {
        Box(Modifier.fillMaxSize()) {
            AppNavigation(Tab.TASKS, {}, 9) { TasksToday() }
            AskOverlay(AskUi(insurance, text = "Move this to Monday and remind me at 9", source = VoiceSource.ON_DEVICE), noAsk)
        }
    }

    @Test fun askListening() = shot("android-ask-listening", dark = true) {
        Box(Modifier.fillMaxSize()) {
            AppNavigation(Tab.TASKS, {}, 9) { TasksToday() }
            AskOverlay(AskUi(insurance, text = "Move this to Monday and ask Jordan for",
                voice = VoiceState.Listening("move this to Monday and ask Jordan for", 0.6f), source = VoiceSource.ON_DEVICE), noAsk)
        }
    }

    /** mail.send_to_self on android.share: the Send to self card for a shared link, labels from the last send picked. */
    @Test fun sendToSelf() = shot("android-send-to-self") {
        val picked = listOf(19L)
        io.github.murtazanasir.mailr.ui.share.SelfShareOverlay(io.github.murtazanasir.mailr.ui.share.SelfUi(
            phase = io.github.murtazanasir.mailr.ui.share.SelfPhase.CONFIRM,
            subject = "How small studios price brand work",
            preview = io.github.murtazanasir.mailr.ui.share.SelfPreview(link = io.github.murtazanasir.mailr.ui.share.SelfLink(
                "https://www.designweekly.example/pricing-brand-work", "How small studios price brand work", "designweekly.example")),
            note = "For the Lumen Coffee quote",
            labels = io.github.murtazanasir.mailr.ui.share.SelfLabels.usable(labels),
            quickLabels = io.github.murtazanasir.mailr.ui.share.SelfLabels.quick(labels, listOf(19L, 20L, 10L), picked),
            selected = picked,
        ), io.github.murtazanasir.mailr.ui.share.SelfShareCallbacks({}, {}, {}, {}, {}, {}, {}, {}))
    }

    /** settings.account_health on android.settings: one account syncing, one that needs its password. */
    @Test fun accountHealth() = shot("android-account-health") {
        fun acct(id: Long, name: String, state: String, text: String) =
            io.github.murtazanasir.mailr.data.api.MailAccount(id, "imap", name, true, io.github.murtazanasir.mailr.data.api.AccountHealth(state, text), ago(4))
        SettingsScreen(Prefs(), myName, myEmail, server, emptyList(), SettingsCallbacks({}, {}, {}, {}, {}, openActivity = {},
            accounts = listOf(acct(1, "Northwind (alex@northwind.example)", "ok", "Up to date"),
                acct(2, "Studio (hello@studio.example)", "signin", "Needs its password again"))))
    }

    @Test fun settings() = shot("android-settings") {
        SettingsScreen(Prefs(look = if (webLook) io.github.murtazanasir.mailr.data.settings.AppLook.WEB else io.github.murtazanasir.mailr.data.settings.AppLook.MATERIAL), myName, myEmail, server, emptyList(), SettingsCallbacks({}, {}, {}, {}, {}, openActivity = {}))
    }

    /** On android.widget: the Today widget (task.complete, agent.dictate) and the 1x1 Talk to Ambrose widget on a home screen. */
    @Test fun todayWidget() {
        RuntimeEnvironment.setQualifiers("$phone-notnight-xxhdpi")
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        val at = { h: Int, m: Int -> workday.atTime(h, m).atZone(zone).toOffsetDateTime().toString() }
        val morning = workday.atTime(9, 10).atZone(zone)
        val model = io.github.murtazanasir.mailr.ui.today.TodayModel.build(listOf(
            CalendarEvent("d1", "Lumen Coffee kickoff", at(9, 0), at(10, 0), calendar = "work", calendarColor = "#1a73e8"),
            CalendarEvent("d2", "Lunch with Jordan", at(12, 30), at(13, 30), calendar = "home", calendarColor = "#0b8043"),
            CalendarEvent("d3", "Piano lesson", at(17, 0), at(18, 0), calendar = "family", calendarColor = "#8e24aa"),
        ), listOf(
            TaskItem("d4", "Renew the studio insurance", workday.minusDays(2).toString(), dueAllDay = true),
            TaskItem("d5", "Send the Q3 numbers to Dana", at(15, 0)),
            TaskItem("d6", "Buy oat milk", workday.toString(), dueAllDay = true),
        ), morning)
        homeScreen(ctx, false, 411, 520, listOf(
            PlacedWidget(16, 24, 379, 360) { io.github.murtazanasir.mailr.ui.today.TodayWidgetContent(model) },
            PlacedWidget(16, 404, 80, 92) { io.github.murtazanasir.mailr.ui.ask.AskWidgetContent(ctx) },
        )).captureRoboImage("screenshots/docs/android-today-widget.png", roborazziOptions = DOCS_SHOT)
    }

    /** order.pause and order.permission on android.settings: Settings → Ambrose → Standing orders. */
    @Test fun standingOrders() = shot("android-standing-orders") {
        val store = object : io.github.murtazanasir.mailr.ui.settings.OrdersStore {
            override suspend fun list() = listOf(
                io.github.murtazanasir.mailr.data.api.StandingOrder(1, "Follow my forwarded notes", builtin = "forwarded_notes", permission = "ask", fromTrusted = true),
                io.github.murtazanasir.mailr.data.api.StandingOrder(2, "Client asks", query = "from:lumencoffee.example OR from:bluefin.example"),
                io.github.murtazanasir.mailr.data.api.StandingOrder(3, "Bills", query = "subject:(invoice OR bill)"),
                io.github.murtazanasir.mailr.data.api.StandingOrder(4, "Morning summary", trigger = "schedule"),
                io.github.murtazanasir.mailr.data.api.StandingOrder(5, "Newsletter digest", query = "category:newsletters", enabled = false),
            )
            override suspend fun setEnabled(id: Long, enabled: Boolean) {}
            override suspend fun setPermission(id: Long, permission: String) {}
        }
        io.github.murtazanasir.mailr.ui.settings.OrdersPage(store, "Ambrose", onBack = {})
    }

    /** trusted.list on android.settings: Settings → Ambrose → Trusted addresses. */
    // A tall phone, so the addresses another assistant handles are in the shot too.
    @Test fun trustedAddresses() = shot("android-trusted-addresses", size = "w411dp-h1240dp-port") {
        val store = object : io.github.murtazanasir.mailr.ui.settings.TrustedStore {
            override suspend fun list() = io.github.murtazanasir.mailr.data.api.TrustedListing(
                addresses = listOf(
                    io.github.murtazanasir.mailr.data.api.TrustedAddress(1, "alex.rivera@lakeside.example", "confirmed",
                        lastMessage = io.github.murtazanasir.mailr.data.api.TrustedLastMessage("Fw: Board meeting", ago(19 * 60), true)),
                    io.github.murtazanasir.mailr.data.api.TrustedAddress(2, "arivera@consulting.example", "pending"),
                ),
                suggestions = listOf(io.github.murtazanasir.mailr.data.api.TrustedSuggestion("alex@rivera-family.example", "Alex Rivera", 4)),
                handledElsewhere = listOf("memory@northwind.example"),
            )
            override suspend fun add(email: String) = io.github.murtazanasir.mailr.data.api.TrustedAddress(3, email)
            override suspend fun confirm(id: Long, code: String) = io.github.murtazanasir.mailr.data.api.TrustedAddress(id, state = "confirmed")
            override suspend fun resend(id: Long) = io.github.murtazanasir.mailr.data.api.TrustedAddress(id)
            override suspend fun remove(id: Long) {}
            override suspend fun setElsewhere(addresses: List<String>) {}
        }
        io.github.murtazanasir.mailr.ui.settings.TrustedPage(store, "Ambrose", onBack = {})
    }

    /** Settings → Activity: read-only on the phone. */
    @Test fun activity() = shot("android-activity") {
        val now = AppClock.now(zone)
        fun ago(m: Long) = now.minusMinutes(m).toOffsetDateTime().toString()
        io.github.murtazanasir.mailr.ui.settings.ActivityScreen(io.github.murtazanasir.mailr.ui.settings.ActivityUi(listOf(
            io.github.murtazanasir.mailr.data.api.ActivityItem("1", ago(5), "you", null, "tasks", "Completed “Buy oat milk”"),
            io.github.murtazanasir.mailr.data.api.ActivityItem("2", ago(30), "ambrose", null, "calendar", "Moved “Piano lesson” to 5 PM"),
            io.github.murtazanasir.mailr.data.api.ActivityItem("3", ago(70), "agent", "Research agent", "mail", "Labelled 3 conversations Research"),
            io.github.murtazanasir.mailr.data.api.ActivityItem("4", ago(60 * 26), "admin", "Sam Lee", "account", "Your administrator added you to the group Studio."),
            io.github.murtazanasir.mailr.data.api.ActivityItem("5", ago(60 * 27), "you", null, "mail", "Archived 12 conversations", undone = true),
        ), loading = false, more = true), {}, {}, {}, now)
    }

    /** The Memory page: To review, About you and the kind chips, on the demo's memories. */
    @Test fun memory() = shot("android-memory") {
        val store = io.github.murtazanasir.mailr.ui.memory.FakeMemoryStore()
        io.github.murtazanasir.mailr.ui.memory.MemoryScreen(io.github.murtazanasir.mailr.ui.memory.MemoryScreenshotTest.ui(store), "Ambrose",
            io.github.murtazanasir.mailr.ui.memory.MemoryCallbacks({}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, openSource = {}),
            now = AppClock.now(zone))
    }

    /** Chats (earlier chats with Ambrose), from the Ambrose top bar: title, last activity and the start of each answer. */
    @Test fun chats() = shot("android-chats") {
        io.github.murtazanasir.mailr.ui.assistant.ChatListScreen(io.github.murtazanasir.mailr.ui.assistant.ChatsUi(loading = false,
            chats = io.github.murtazanasir.mailr.ui.assistant.ChatsScreenshotTest.CHATS), "Ambrose", "c2",
            io.github.murtazanasir.mailr.ui.assistant.ChatsScreenshotTest.noop, now = AppClock.now(zone))
    }

    /** A person's card as a sheet over the Memory page: details, VIP and Ambrose's memory. */
    @Test fun personCard() {
        val store = io.github.murtazanasir.mailr.ui.memory.FakeMemoryStore()
        shot("android-person-card", then = {
            compose.until("\"Ambrose's memory\" shown") { compose.onAllNodesWithText("Ambrose's memory").fetchSemanticsNodes().isNotEmpty() }
        }) {
            io.github.murtazanasir.mailr.ui.memory.MemoryScreen(io.github.murtazanasir.mailr.ui.memory.MemoryScreenshotTest.ui(store), "Ambrose",
                io.github.murtazanasir.mailr.ui.memory.MemoryCallbacks({}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, openSource = {}),
                now = AppClock.now(zone))
            io.github.murtazanasir.mailr.ui.memory.PersonCardSheet(io.github.murtazanasir.mailr.ui.memory.PersonTarget.Entry(2), store, "Ambrose",
                { it == "priya.shah@northwind.example" }, onClose = {})
        }
    }
}

/** The demo's review card as the server builds it (backend demo.py _seed_review, agent/review.py card()). */
private const val DOCS_REVIEW_CARD = """{"id":6,"actor":"Ambrose","action":"mail.trash","status":"pending",
 "preview":{"title":"Make 11 changes from the review of 14 conversations","kind":"review",
  "lines":["Move to Trash: 10 (Fieldnote Paper Co. 6, Atlas Outfitters 4)","Mark read: 1 (Atlas Outfitters 1)","Kept: 2 still useful","Unsure, left alone: 1"],
  "review":{"review_id":1,"scope":"All Mail matching \"from:(fieldnote-paper.example OR atlas-outfitters.example)\"","total":14,"model":"mailr-demo",
   "groups":[
    {"move":"trash","count":10,"senders":[
      {"from":"Fieldnote Paper Co.","count":6,"email":"hello@fieldnote-paper.example","suggest_filter":true,"conversation_ids":[33,25,20,18,15,12],"subjects":["New dot-grid colours","Free shipping this weekend","Back to school: 20% off"],
       "conversations":[{"conversation_id":33,"subject":"New dot-grid colours","date":"2026-10-14"},{"conversation_id":25,"subject":"Free shipping this weekend","date":"2026-10-03"},
        {"conversation_id":20,"subject":"Back to school: 20% off","date":"2026-09-22"},{"conversation_id":18,"subject":"Pens restocked","date":"2026-09-15"},
        {"conversation_id":15,"subject":"Your loyalty points","date":"2026-09-02"},{"conversation_id":12,"subject":"Summer sketchbook sale","date":"2026-08-20"}]},
      {"from":"Atlas Outfitters","count":4,"email":"deals@atlas-outfitters.example","suggest_filter":true,"conversation_ids":[19,16,13,11],"subjects":["Trail season sale","New tents are in","Members save 15%"],
       "conversations":[{"conversation_id":19,"subject":"Trail season sale","date":"2026-10-10"},{"conversation_id":16,"subject":"New tents are in","date":"2026-09-28"},
        {"conversation_id":13,"subject":"Members save 15%","date":"2026-09-12"},{"conversation_id":11,"subject":"Camp stove clearance","date":"2026-08-30"}]}]},
    {"move":"read","count":1,"senders":[
      {"from":"Atlas Outfitters","count":1,"conversation_ids":[9],"subjects":["Winter layers preview"],
       "conversations":[{"conversation_id":9,"subject":"Winter layers preview","date":"2026-10-08"}]}]}],
   "set_aside":{},
   "kept":[{"conversation_id":70,"from":"Fieldnote Paper Co.","subject":"25% off notebooks this week","reason":"offer still runs this week"},
           {"conversation_id":64,"from":"Atlas Outfitters","subject":"The fall layering sale is here","reason":"sale runs through the weekend"}],"kept_count":2,
   "unsure":[{"conversation_id":7,"from":"Atlas Outfitters","subject":"Your gear checklist","reason":"may be a saved checklist"}],"unsure_count":1}}}"""
