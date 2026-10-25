// ACT-SURFACES: android.widget
package io.github.murtazanasir.mailr

import android.app.Application
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.glance.appwidget.ExperimentalGlanceRemoteViewsApi
import androidx.glance.appwidget.GlanceRemoteViews
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.data.reminders.Kind
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.data.voice.VoiceSource
import io.github.murtazanasir.mailr.data.voice.VoiceState
import io.github.murtazanasir.mailr.ui.ask.AskCallbacks
import io.github.murtazanasir.mailr.ui.ask.AskOverlay
import io.github.murtazanasir.mailr.ui.ask.AskUi
import io.github.murtazanasir.mailr.ui.ask.AskWidgetContent
import io.github.murtazanasir.mailr.ui.calendar.AlertCallbacks
import io.github.murtazanasir.mailr.ui.calendar.ReminderAlertScreen
import io.github.murtazanasir.mailr.ui.settings.dictationItems
import io.github.murtazanasir.mailr.ui.settings.reminderAlertItems
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.coroutines.runBlocking
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * The ask surfaces, light and dark: the ask capture (android.ask) in its states,
 * the full-screen alert (android.fullscreen) for a task and an event, the "Ask mailr" widget
 * (android.widget, agent.dictate) and the new Settings rows.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class AskScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()
    private val mode get() = if (dark) "dark" else "light"

    private fun shot(name: String, content: @Composable () -> Unit) {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) { content() }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/ask-$mode-$name.png")
    }

    private val noop = AskCallbacks({}, {}, {}, {}, {}, {}, {}, { false }, {})
    private val task = AskAbout.Task("t1", "Send the Q3 numbers to Dana")

    @Test fun captureWithTask() = shot("capture-task") { AskOverlay(AskUi(task, source = VoiceSource.ON_DEVICE), noop) }

    @Test fun captureListening() = shot("capture-listening") {
        AskOverlay(AskUi(task, text = "Move this to Friday and email Dana", voice = VoiceState.Listening("move this to Friday and email Dana", 0.6f),
            source = VoiceSource.ON_DEVICE), noop)
    }

    @Test fun captureHandsFree() = shot("capture-handsfree") {
        AskOverlay(AskUi(AskAbout.Event("e1", null, "Budget review"), text = "Tell the guests I will be ten minutes late",
            voice = VoiceState.Listening("Tell the guests I will be ten minutes late", 0.3f), source = VoiceSource.ON_DEVICE, handsFree = true), noop)
    }

    @Test fun captureTranscribing() = shot("capture-transcribing") {
        AskOverlay(AskUi(null, voice = VoiceState.Transcribing, source = VoiceSource.SERVER), noop)
    }

    @Test fun captureEdited() = shot("capture-edited") {
        AskOverlay(AskUi(task, text = "Move this to Friday and email Dana about it", voice = VoiceState.Heard("move this to Friday and email Dana about it"),
            source = VoiceSource.SERVER), noop)
    }

    @Test fun captureFailed() = shot("capture-failed") {
        AskOverlay(AskUi(null, voice = VoiceState.Failed("Voice typing for this language isn't on this phone yet.", download = true),
            source = VoiceSource.ON_DEVICE), noop)
    }

    @Test fun captureTypingOnly() = shot("capture-typing") {
        AskOverlay(AskUi(AskAbout.Conversation(1, "Re: Q3 numbers"), text = "Draft a short yes", source = VoiceSource.NONE), noop)
    }

    private val now = ZonedDateTime.of(2026, 10, 2, 14, 52, 0, 0, ZoneId.of("America/Chicago"))
    private val alertCb = AlertCallbacks({}, {}, {}, {}, {}, {}, {})

    @Test fun fullScreenTask() = shot("fullscreen-task") {
        val due = now.plusMinutes(8).toInstant().toEpochMilli()
        ReminderAlertScreen(ReminderEntity("t|t1|x|0", Kind.TASK, "t1", null, "Send the Q3 numbers to Dana", "Due today, 3:00 PM",
            due, due, due, null, RState.SHOWN, updatedAt = 0), null, now.toInstant().toEpochMilli(), alertCb, now.zone)
    }

    @Test fun fullScreenEvent() = shot("fullscreen-event") {
        val start = now.plusMinutes(8).toInstant().toEpochMilli()
        ReminderAlertScreen(ReminderEntity("e|e1|x|10", Kind.EVENT, "e1", "x", "Budget review", "3:00 – 4:00 PM · Room 204",
            start, start, start, start + 3_600_000L, RState.SHOWN, updatedAt = 0), "https://meet.example.com/abc-defg-hij",
            now.toInstant().toEpochMilli(), alertCb, now.zone)
    }

    @Test fun settingsRows() = shot("settings") {
        val p = Prefs(fullScreenAlerts = false, remindAgain = 10, morningAgenda = true, serverDictation = true)
        LazyColumn { reminderAlertItems(p, update = {}); dictationItems(p) {} }
    }

    /** The Glance widget, rendered to RemoteViews and applied as the launcher would. */
    @OptIn(ExperimentalGlanceRemoteViewsApi::class)
    @Test fun widget() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        val rv = runBlocking { GlanceRemoteViews().compose(ctx, DpSize(320.dp, 64.dp)) { AskWidgetContent(ctx) }.remoteViews }
        val frame = FrameLayout(ctx).apply {
            setBackgroundColor(if (dark) 0xFF202124.toInt() else 0xFFDDE3EA.toInt())
            setPadding(24, 24, 24, 24)
        }
        val v: View = rv.apply(ctx, frame)
        frame.addView(v, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, (64 * ctx.resources.displayMetrics.density).toInt()))
        val w = (411 * ctx.resources.displayMetrics.density).toInt()
        frame.measure(View.MeasureSpec.makeMeasureSpec(w, View.MeasureSpec.EXACTLY), View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED))
        frame.layout(0, 0, frame.measuredWidth, frame.measuredHeight)
        val bmp = android.graphics.Bitmap.createBitmap(frame.measuredWidth, frame.measuredHeight, android.graphics.Bitmap.Config.ARGB_8888)
        frame.draw(android.graphics.Canvas(bmp))
        bmp.captureRoboImage("screenshots/ask-$mode-widget.png")
    }
}
