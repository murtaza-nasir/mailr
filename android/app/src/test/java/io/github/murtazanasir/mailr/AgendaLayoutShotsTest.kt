package io.github.murtazanasir.mailr

import android.app.Application
import io.github.murtazanasir.mailr.ui.agenda.AgendaModel
import io.github.murtazanasir.mailr.ui.agenda.AgendaStyle
import io.github.murtazanasir.mailr.ui.agenda.AgendaWidgetContent
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** Agenda widget shots ([AgendaShots]): Few and many items, and the day and time columns. Light and dark. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class AgendaLayoutShotsTest(dark: Boolean) : AgendaShots(dark) {
    /** A few items: the top block is the same at 4×3 and 4×2; the lists end where the items end. */
    @Test fun few() {
        val m = fewModel()
        AgendaStyle.entries.forEach { st ->
            widget("few-${st.key}-4x3", 373, 315) { AgendaWidgetContent(m, st) }
            widget("few-${st.key}-4x2", 373, 210) { AgendaWidgetContent(m, st) }
        }
    }

    /** Many items at 4×2: each list is cut at the bottom edge and scrolls on its own. */
    @Test fun many() {
        val m = manyModel()
        AgendaStyle.entries.forEach { st -> widget("many-${st.key}-4x2", 373, 210) { AgendaWidgetContent(m, st) } }
    }

    /**
     * "Line up days and times": on a 12-hour clock, where times differ
     * most in width ("9 AM", "12:30 PM"), at the widest and the narrow size; and turned off.
     * Numeral at 3x2 is left out: the extra events (five and six days ahead) do not fit there, so the
     * shot was AgendaStylesShotsTest's numeral-12h-3x2 (and, on a 24-hour clock, numeral-3x2) pixel for pixel.
     */
    @Test fun columns() {
        val m = AgendaModel.build(events + listOf(
            ev("y1", "Introduction to Statistics", at(5, 9), at(5, 10), BLUE),
            ev("y2", "CHEM 101 Exam (lab)", at(5, 17, 35), at(5, 19), GRAPE),
            ev("y3", "Linear Algebra", at(6, 19, 5), at(6, 20), PEACOCK),
        ), tasks, now, 7, h24 = false, weather = weather)
        AgendaStyle.entries.forEach { st ->
            widget("columns-12h-${st.key}-4x3", 373, 315) { AgendaWidgetContent(m, st) }
            if (st != AgendaStyle.NUMERAL) widget("columns-12h-${st.key}-3x2", 280, 210) { AgendaWidgetContent(m, st) }
        }
        widget("columns-off-numeral-4x3", 373, 315) { AgendaWidgetContent(m, AgendaStyle.NUMERAL, columns = false) }
    }
}
