package io.github.murtazanasir.mailr.ui.tasks

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.remember
import androidx.compose.ui.focus.FocusRequester
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTextReplacement
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * Typing in Tasks → Today once crashed
 * the app. The crash was Android's ICU regex engine rejecting the Java `(?U)` flag on the first
 * keystroke (fixed in 0.4.9, AndroidRegexFlagsTest guards it). This types realistic lines into the
 * quick-add bar one character at a time, pastes, clears, and parses every prefix at several times
 * of day, so a parser that throws on partial input fails here.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class QuickAddTypingTest {
    @get:Rule val compose = createComposeRule()

    private val zone = ZoneId.of("America/Chicago")
    private val now = ZonedDateTime.of(2026, 10, 3, 1, 19, 0, 0, zone)
    private val lists = listOf(
        CalendarCollection("L1", "Work", "#039be5", tasks = true, calendarId = 1),
        CalendarCollection("L2", "Home projects", "#33b679", tasks = true, calendarId = 2),
    )

    private val lines = listOf(
        "Call Dana fri 3pm !! @phone", "tom", "tomorrow", "every mon", "every! 3 days", "#Work", "#Home projects",
        "Pay rent every month on the 1st !! #Work", "Buy milk 🥛 tmrw 9", "Dentist 12–14 oct", "Review at 15:3",
        "Call mom at 9 ", "Remind me in 2h", "!!!", "@", "#", "every", "3:", "fri 3p", "next week", "Ünïcödé façade 10am",
        "a b c", "👩‍👩‍👧 party sat 7pm", "\"quoted next week\" fri",
    )

    @Test fun typingCharByCharPastingAndClearingNeverCrashes() {
        compose.setContent {
            MaterialTheme {
                val focus = remember { FocusRequester() }
                TaskQuickAddBar(lists, QuickPrefill(day = LocalDate.of(2026, 10, 3)), { now }, focus,
                    onAdd = { _, _ -> }, onDetails = { _, _ -> }, onClearPrefill = {})
            }
        }
        val field = compose.onNodeWithContentDescription("Quick add")
        for (line in lines) {
            // One code point at a time, as a keyboard sends them (an emoji is one key).
            var i = 0
            while (i < line.length) {
                val n = Character.charCount(line.codePointAt(i))
                field.performTextInput(line.substring(i, i + n))
                compose.waitForIdle()
                i += n
            }
            field.assert(hasText(line, substring = true))
            field.performTextClearance()
            compose.waitForIdle()
            field.performTextReplacement(line)  // a paste
            compose.waitForIdle()
            field.performTextClearance()
        }
    }

    @Test fun everyPrefixParsesAtAnyTimeOfDay() {
        val times = listOf(now, now.withHour(9).withMinute(0), now.withHour(15).withMinute(59), now.withHour(23).withMinute(45))
        for (t in times) for (line in lines) for (end in 1..line.length) {
            val text = line.substring(0, end)
            val r = QuickTask.parse(text, t, lists)
            r.tokens.forEach { tok -> assertTrue("$text: token ${tok.text} at ${tok.start}..${tok.end}", tok.start in 0..tok.end && tok.end <= text.length) }
            QuickTask.plan(r, "uid", zone, QuickPrefill(day = t.toLocalDate()))
        }
    }
}
