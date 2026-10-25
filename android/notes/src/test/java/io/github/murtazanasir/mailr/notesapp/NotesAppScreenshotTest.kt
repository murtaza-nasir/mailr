package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import android.widget.FrameLayout
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.glance.GlanceTheme
import androidx.glance.appwidget.ExperimentalGlanceRemoteViewsApi
import androidx.glance.appwidget.GlanceRemoteViews
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.NativeConfig
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.notes.FakeNotesServer
import io.github.murtazanasir.mailr.notesapp.capture.CaptureMode
import io.github.murtazanasir.mailr.notesapp.capture.CaptureSheet
import io.github.murtazanasir.mailr.notesapp.capture.ShareSheet
import io.github.murtazanasir.mailr.notesapp.data.NotesPrefs
import io.github.murtazanasir.mailr.notesapp.data.ThemeChoice
import io.github.murtazanasir.mailr.notesapp.ui.NotesAppRoot
import io.github.murtazanasir.mailr.notesapp.ui.NotesBrand
import io.github.murtazanasir.mailr.notesapp.ui.NotesTheme
import io.github.murtazanasir.mailr.notesapp.widget.CaptureContent
import io.github.murtazanasir.mailr.notesapp.widget.ChecklistContent
import io.github.murtazanasir.mailr.notesapp.widget.TableContent
import io.github.murtazanasir.mailr.notesapp.widget.WidgetModels
import io.github.murtazanasir.mailr.testing.until
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingCallbacks
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingScreen
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingState
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The Notes app on a phone, light and dark: sign-in, the grid, quick capture, the share sheet and
 * the widgets (rendered through Glance's RemoteViews). Output: notes/screenshots/notesapp-<theme>-<screen>.png;
 * the light ones also go to notes/screenshots/docs/ for the docs (site/shots.yaml).
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class NotesAppScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule(order = Int.MIN_VALUE) val graphs = NotesTestGraph()
    @get:Rule val compose = createComposeRule()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: NotesGraph
    private val ctx get() = ApplicationProvider.getApplicationContext<Application>()
    private val theme get() = if (dark) "dark" else "light"
    private val look get() = NotesPrefs.Look(if (dark) ThemeChoice.DARK else ThemeChoice.LIGHT, false)

    @Before fun setUp() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        fake = graphs.closeLast(object : AutoCloseable {
            val f = FakeNotesServer().start()
            override fun close() = f.close()
        }).f
    }

    private fun signIn() {
        SessionStore(ctx).apply {
            server = fake.server.url("/").toString().trimEnd('/')
            save("tok-1", "csrf-1", "dana@example.com", "Dana Example")
        }
        g = graphs.graph(ctx)
        g.prefs.setLook(look)
    }

    private fun seed(): FakeNotesServer.N {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "flour", "bread"), sections = listOf("Costco", "Corner market"))
        shop.color = "sage"; shop.pinned = true; shop.shopping = true
        val items = fake.items[shop.id]!!
        items[0].qty = buildJsonObject { put("amount", "2"); put("unit", JsonPrimitive(null as String?)) }
        items[1].qty = buildJsonObject { put("amount", "500"); put("unit", "g") }
        val costco = fake.sections[shop.id]!![0].id
        val corner = fake.sections[shop.id]!![1].id
        items += FakeNotesServer.I("0192aaaa-0000-7000-8000-000000000011", "paper towels", costco, order = "b1")
        items += FakeNotesServer.I("0192aaaa-0000-7000-8000-000000000012", "avocados", costco, order = "b2",
            qty = buildJsonObject { put("amount", "3"); put("unit", JsonPrimitive(null as String?)) })
        items += FakeNotesServer.I("0192aaaa-0000-7000-8000-000000000013", "limes", corner, order = "c1")
        items += FakeNotesServer.I("0192aaaa-0000-7000-8000-000000000014", "eggs", null, order = "a9", checked = true,
            checkedAt = "2026-10-05T09:00:00+00:00")
        fake.note("text", "Trip ideas").also { it.body = "Lake Tahoe in October\nBook the cabin early\nAsk Sam about the boat"; it.labels = listOf("Travel") }
        fake.note("checklist", "Packing list", listOf("socks", "charger", "passport")).also { it.color = "peacock" }
        fake.note("text", "Gate code").also { it.body = "4417 at the side door"; it.color = "banana" }
        fake.table("Workouts", listOf("Exercise" to "text", "Weight" to "number", "Date" to "date"), listOf(
            mapOf("c0" to "Squat", "c1" to "82.5", "c2" to "2026-10-03"), mapOf("c0" to "Bench press", "c1" to "60", "c2" to "2026-10-03"),
            mapOf("c0" to "Deadlift", "c1" to "100", "c2" to "2026-10-04")))
        fake.labels += listOf("Travel", "Home")
        runBlocking { g.notes.repo.pull() }
        return shop
    }

    private fun shot(name: String) {
        compose.waitForIdle()
        compose.onRoot().captureRoboImage("screenshots/notesapp-$theme-$name.png")
        if (!dark) compose.onRoot().captureRoboImage("screenshots/docs/android-notesapp-$name.png", roborazziOptions = DOCS_SHOT)
    }

    private fun waitText(t: String) = compose.until("text: $t") { compose.onAllNodes(hasText(t, substring = true)).fetchSemanticsNodes().isNotEmpty() }
    @Test fun signin() {
        compose.setContent {
            NotesTheme(look) {
                OnboardingScreen(OnboardingState("https://mail.example.com",
                    NativeConfig(true, "https://pocket.example.com", "mailr-android", "io.github.murtazanasir.mailr.notes:/oauth2redirect", providerName = "Pocket ID")),
                    OnboardingCallbacks({}, {}, {}, {}, { _, _ -> }), NotesBrand)
            }
        }
        waitText("Sign in with")
        shot("signin")
    }

    @Test fun grid() {
        signIn()
        seed()
        compose.setContent { NotesAppRoot(g, MutableStateFlow(null)) {} }
        waitText("Workouts")
        shot("grid")
    }

    @Test fun capture() {
        signIn()
        val shop = seed()
        g.prefs.defaultList = shop.id
        compose.setContent { NotesTheme(look) { CaptureSheet(g.notes.repo, g.prefs, CaptureMode.ADD, shop.id, { }) { } } }
        compose.until("tag \"capture-text\" shown") { compose.onAllNodes(hasTestTag("capture-text")).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithTag("capture-text").performTextInput("Corner market: 2 limes, coffee")
        waitText("Add 2 items to Shopping")
        shot("capture")
    }

    @Test fun share() {
        signIn()
        g.prefs.opened("note", seed().id)
        compose.setContent {
            NotesTheme(look) { ShareSheet(g.notes.repo, g.prefs, "Lemon cake\nflour\n4 eggs\n200 g butter, lemons", null) { } }
        }
        compose.until("tag \"share-LIST\" shown") { compose.onAllNodes(hasTestTag("share-LIST")).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithTag("share-LIST").performClick()
        waitText("items to")
        shot("share")
    }

    @OptIn(ExperimentalGlanceRemoteViewsApi::class)
    @Test fun widgets() {
        signIn()
        val shop = seed()
        val table = fake.tables.values.first().id
        val (list, rows) = runBlocking {
            WidgetModels.checklist(g.notes.repo, shop.id, null, withUsual = false) to WidgetModels.table(g.notes.repo, table, listOf("c1"))
        }
        val remote = GlanceRemoteViews()
        val views = runBlocking {
            listOf(
                remote.compose(ctx, DpSize(300.dp, 330.dp)) { GlanceTheme { ChecklistContent(list, 1) } }.remoteViews,
                remote.compose(ctx, DpSize(300.dp, 190.dp)) { GlanceTheme { TableContent(rows, 2) } }.remoteViews,
                remote.compose(ctx, DpSize(300.dp, 64.dp)) { GlanceTheme { CaptureContent(true, shop.id, "Shopping") } }.remoteViews,
            )
        }
        compose.setContent {
            // A plain wallpaper: the widgets as a launcher draws them.
            Box(Modifier.fillMaxSize().background(Brush.verticalGradient(
                if (dark) listOf(Color(0xFF1B2A3A), Color(0xFF0E1620)) else listOf(Color(0xFF9CC3E6), Color(0xFFD9E8F5))))) {
                Column(Modifier.fillMaxWidth().padding(top = 48.dp, start = 32.dp, end = 32.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
                    listOf(330, 190, 64).zip(views).forEach { (h, rv) ->
                        AndroidView(factory = { c -> FrameLayout(c).apply { addView(rv.apply(c, this)) } },
                            modifier = Modifier.fillMaxWidth().height(h.dp).clip(RoundedCornerShape(16.dp)))
                    }
                }
            }
        }
        shot("widgets")
    }

    /** The checklist widget's usual chips and the waiting count, as a launcher draws it (not a docs shot). */
    @OptIn(ExperimentalGlanceRemoteViewsApi::class)
    @Test fun widgetUsualChips() {
        signIn()
        val shop = seed()
        fake.notes[shop.id]!!.suggestUsual = true
        fake.usual += listOf("coffee", "eggs", "bananas", "tea")
        val list = runBlocking {
            g.notes.repo.pull()
            fake.offline = true
            g.notes.repo.addItems(shop.id, "dish soap")
            g.notes.repo.drain()
            fake.offline = false
            WidgetModels.checklist(g.notes.repo, shop.id, null)
        }
        val rv = runBlocking { GlanceRemoteViews().compose(ctx, DpSize(300.dp, 360.dp)) { GlanceTheme { ChecklistContent(list, 1) } }.remoteViews }
        compose.setContent {
            Box(Modifier.fillMaxSize().background(Brush.verticalGradient(
                if (dark) listOf(Color(0xFF1B2A3A), Color(0xFF0E1620)) else listOf(Color(0xFF9CC3E6), Color(0xFFD9E8F5))))) {
                AndroidView(factory = { c -> FrameLayout(c).apply { addView(rv.apply(c, this)) } },
                    modifier = Modifier.fillMaxWidth().padding(top = 48.dp, start = 32.dp, end = 32.dp).height(360.dp).clip(RoundedCornerShape(16.dp)))
            }
        }
        compose.waitForIdle()
        compose.onRoot().captureRoboImage("screenshots/notesapp-$theme-widget-usual.png")
    }
}
