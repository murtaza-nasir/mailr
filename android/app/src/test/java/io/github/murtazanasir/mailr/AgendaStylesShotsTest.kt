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

/** Agenda widget shots ([AgendaShots]): The four styles at the three sizes, on a 24-hour and (numeral duotone) a 12-hour clock. Light and dark. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class AgendaStylesShotsTest(dark: Boolean) : AgendaShots(dark) {
    @Test fun glass() = style(AgendaStyle.GLASS)

    @Test fun paper() = style(AgendaStyle.PAPER)

    @Test fun week() = style(AgendaStyle.WEEK)

    @Test fun numeral() = style(AgendaStyle.NUMERAL)

    /** Numeral duotone on a 12-hour clock at every size: the hourly labels ("3 pm") fit; narrow shows three hours. */
    @Test fun numeral12h() {
        val m = AgendaModel.build(events, tasks, now, 5, h24 = false, weather = weather)
        SIZES.forEach { (size, wh) -> widget("numeral-12h-$size", wh.first, wh.second) { AgendaWidgetContent(m, AgendaStyle.NUMERAL) } }
    }
}
