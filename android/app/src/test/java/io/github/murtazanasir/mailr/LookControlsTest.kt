package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SnackbarData
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarVisuals
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.assertIsNotSelected
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextReplacement
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.look.BannerTone
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import io.github.murtazanasir.mailr.look.DatePicker
import io.github.murtazanasir.mailr.look.LinearProgressIndicator
import io.github.murtazanasir.mailr.look.RadioButton
import io.github.murtazanasir.mailr.look.SegmentedButton
import io.github.murtazanasir.mailr.look.SelectField
import io.github.murtazanasir.mailr.look.SingleChoiceSegmentedButtonRow
import io.github.murtazanasir.mailr.look.Snackbar
import io.github.murtazanasir.mailr.look.TextField
import io.github.murtazanasir.mailr.look.TimePicker
import io.github.murtazanasir.mailr.look.WhenText
import io.github.murtazanasir.mailr.look.bannerColors
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneOffset
import java.util.Locale

/**
 * The web look's controls (io.github.murtazanasir.mailr.look: Choices, Feedback, Fields, Pickers): what they do
 * (TalkBack roles, typed dates and times, the grid writing Material's picker state) and how they look,
 * light and dark, with one right-to-left sheet. References: screenshots/look-controls-*.png.
 */
@OptIn(ExperimentalMaterial3Api::class)
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class LookControlsTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()
    private val mode get() = if (dark) "dark" else "light"
    private lateinit var locale: Locale

    @Before fun pin() {
        locale = Locale.getDefault()
        Locale.setDefault(Locale.US)
        AppClock.pin(Instant.parse("2026-10-09T15:00:00Z"))
    }

    @After fun unpin() {
        Locale.setDefault(locale)
        AppClock.reset()
    }

    private fun show(web: Boolean = true, rtl: Boolean = false, content: @Composable () -> Unit) {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        compose.setContent {
            CompositionLocalProvider(LocalLayoutDirection provides if (rtl) LayoutDirection.Rtl else LayoutDirection.Ltr) {
                MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false, webLook = web) {
                    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) { content() }
                }
            }
        }
        // The spinners and the sliding bar stand still for the picture.
        compose.mainClock.autoAdvance = false
        compose.mainClock.advanceTimeBy(200)
    }

    private class Toast(override val message: String, override val actionLabel: String?, override val withDismissAction: Boolean) :
        SnackbarData, SnackbarVisuals {
        override val visuals: SnackbarVisuals get() = this
        override val duration = SnackbarDuration.Indefinite
        override fun performAction() {}
        override fun dismiss() {}
    }

    @Composable
    private fun Choices() {
        var going by remember { mutableIntStateOf(0) }
        Text("Going?", style = MaterialTheme.typography.bodyMedium)
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            listOf("Yes", "Maybe", "No").forEachIndexed { i, l ->
                SegmentedButton(going == i, { going = i }, SegmentedButtonDefaults.itemShape(i, 3)) { Text(l) }
            }
        }
        var pick by remember { mutableIntStateOf(1) }
        listOf("Only this event", "All events").forEachIndexed { i, l ->
            Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                RadioButton(pick == i, { pick = i }); Text(l)
            }
        }
    }

    @Test fun gallery() {
        show {
            Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Choices()
                Row(horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    CircularProgressIndicator()
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                    CircularProgressIndicator(progress = { 0.6f })
                    LinearProgressIndicator(progress = { 0.4f }, modifier = Modifier.weight(1f))
                }
                LinearProgressIndicator(Modifier.fillMaxWidth())
                Snackbar(Toast("Archived 3 conversations", "Undo", withDismissAction = true))
                BannerTone.entries.forEach { tone ->
                    val (bg, fg) = bannerColors(tone, Color.Gray, Color.Black)
                    Surface(color = bg, contentColor = fg, shape = MaterialTheme.shapes.medium) {
                        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Icon(Icons.Outlined.CloudOff, null); Text("${tone.name}: changes wait on this phone.")
                        }
                    }
                }
                SelectField("Event", onClick = {}, leadingIcon = { Icon(Icons.Outlined.CalendarMonth, null) }, supporting = { Text("2:00 – 3:00 PM") }) {
                    Text("Budget review")
                }
                TextField("", {}, placeholder = { Text("Add subtask") }, singleLine = true,
                    colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent))
                TextField("Send the Q3 numbers", {}, isError = true, supportingText = { Text("A title is needed") },
                    colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent,
                        errorContainerColor = Color.Transparent))
            }
        }
        compose.onRoot().captureRoboImage("screenshots/look-controls-$mode.png")
    }

    @Test fun pickers() {
        show {
            val date = rememberDatePickerState(initialSelectedDateMillis = LocalDate.of(2026, 10, 14).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli())
            val time = rememberTimePickerState(9, 30)
            Column {
                DatePicker(date)
                Column(Modifier.padding(16.dp)) { TimePicker(time) }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/look-pickers-$mode.png")
    }

    /** Day cells are 48 dp tall and 48 dp touch targets, also in the dialog on a 320 dp phone (272 dp wide). */
    @Test fun dayCellsAre48dpTargetsOnASmallPhone() {
        show {
            val date = rememberDatePickerState(initialSelectedDateMillis = LocalDate.of(2026, 10, 14).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli())
            // A Column: the Surface would stretch a direct child to the whole screen.
            Column { androidx.compose.foundation.layout.Box(Modifier.width(272.dp)) { DatePicker(date) } }
        }
        val density = compose.density.density
        val first = LocalDate.of(2026, 9, 27) // the grid's first Sunday
        (0 until 42).forEach { i ->
            val node = compose.onNodeWithContentDescription(first.plusDays(i.toLong()).format(java.time.format.DateTimeFormatter.ofLocalizedDate(java.time.format.FormatStyle.FULL)))
                .fetchSemanticsNode()
            val cell = node.boundsInRoot
            val touch = node.touchBoundsInRoot
            assertEquals(48f, cell.height / density, 0.6f)
            assertTrue("day $i touch ${touch.width / density}x${touch.height / density}dp", touch.width / density >= 47.9f && touch.height / density >= 47.9f)
            assertTrue("day $i inside the grid", cell.right / density <= 272.5f)
        }
        compose.onRoot().captureRoboImage("screenshots/look-date-narrow-$mode.png")
    }

    @Test fun rightToLeft() {
        show(rtl = true) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Choices()
                LinearProgressIndicator(progress = { 0.3f }, modifier = Modifier.fillMaxWidth())
                SelectField("Folder", onClick = {}) { Text("Meetings") }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/look-controls-rtl-$mode.png")
    }

    @Test fun segmentsAndRadiosAreRadioButtons() {
        show { Column { Choices() } }
        val radios = compose.onAllNodes(SemanticsMatcher.expectValue(SemanticsProperties.Role, Role.RadioButton))
        assertEquals("three segments and two radio buttons", 5, radios.fetchSemanticsNodes().size)
        compose.onNodeWithText("Maybe").performClick()
        compose.mainClock.advanceTimeBy(100)
        compose.onNodeWithText("Maybe").assertIsSelected()
        compose.onNodeWithText("Yes").assertIsNotSelected()
    }

    @Test fun typedDateAndTheGridWriteMaterialsState() {
        var picked: Long? = null
        var at: Pair<Int, Int>? = null
        show {
            val date = rememberDatePickerState(initialSelectedDateMillis = LocalDate.of(2026, 10, 14).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli())
            val time = rememberTimePickerState(9, 30)
            picked = date.selectedDateMillis
            at = time.hour to time.minute
            Column { DatePicker(date); TimePicker(time) }
        }
        compose.onNodeWithText("Oct 14, 2026").performTextReplacement("Nov 3, 2026")
        compose.mainClock.advanceTimeBy(100)
        assertEquals(LocalDate.of(2026, 11, 3), Instant.ofEpochMilli(picked!!).atZone(ZoneOffset.UTC).toLocalDate())
        compose.onNodeWithText("November 2026").assertExists()
        compose.onNodeWithContentDescription("Thursday, November 19, 2026").performClick()
        compose.mainClock.advanceTimeBy(100)
        assertEquals(LocalDate.of(2026, 11, 19), Instant.ofEpochMilli(picked!!).atZone(ZoneOffset.UTC).toLocalDate())
        compose.onNodeWithText("Nov 19, 2026").assertExists()
        compose.onNodeWithText("9:30 AM").performTextReplacement("4:15 pm")
        compose.mainClock.advanceTimeBy(100)
        assertEquals(16 to 15, at)
        // Android's clock is one tap away.
        compose.onNodeWithContentDescription("Pick on the clock").performClick()
        compose.mainClock.advanceTimeBy(100)
        compose.onNodeWithContentDescription("Type the time").assertExists()
    }

    @Test fun materialLookIsMaterials() {
        show(web = false) { Column { Choices(); CircularProgressIndicator() } }
        val radios = compose.onAllNodes(SemanticsMatcher.expectValue(SemanticsProperties.Role, Role.RadioButton))
        assertEquals(5, radios.fetchSemanticsNodes().size)
    }

    @Test fun readsTypedDates() {
        val us = Locale.US
        assertEquals(LocalDate.of(2026, 11, 3), WhenText.date("Nov 3, 2026", us, 2026))
        assertEquals(LocalDate.of(2026, 11, 3), WhenText.date("11/3/26", us, 2026))
        assertEquals(LocalDate.of(2026, 11, 3), WhenText.date("2026-11-03", us, 2026))
        assertEquals(LocalDate.of(2027, 11, 3), WhenText.date("11/3", us, 2027))
        assertEquals(LocalDate.of(2026, 11, 3), WhenText.date("nov 3", us, 2026))
        assertEquals(LocalDate.of(2026, 11, 3), WhenText.date("3 November 2026", us, 2026))
        assertEquals(LocalDate.of(2026, 11, 3), WhenText.date("3.11.26", Locale.GERMANY, 2026))
        assertEquals(LocalDate.of(2026, 11, 3), WhenText.date("3.11", Locale.GERMANY, 2026))
        assertNull(WhenText.date("soon", us, 2026))
        assertNull(WhenText.date("", us, 2026))
    }

    @Test fun readsTypedTimes() {
        val us = Locale.US
        assertEquals(LocalTime.of(9, 0), WhenText.time("9", us))
        assertEquals(LocalTime.of(9, 30), WhenText.time("9:30", us))
        assertEquals(LocalTime.of(9, 30), WhenText.time("930", us))
        assertEquals(LocalTime.of(21, 30), WhenText.time("9:30 PM", us))
        assertEquals(LocalTime.of(21, 30), WhenText.time("9.30pm", us))
        assertEquals(LocalTime.of(0, 15), WhenText.time("12:15 am", us))
        assertEquals(LocalTime.of(12, 0), WhenText.time("12pm", us))
        assertEquals(LocalTime.of(21, 30), WhenText.time("21:30", us))
        assertNull(WhenText.time("25:00", us))
        assertNull(WhenText.time("13 pm", us))
        assertNull(WhenText.time("9:75", us))
        assertNull(WhenText.time("noon-ish", us))
    }
}
