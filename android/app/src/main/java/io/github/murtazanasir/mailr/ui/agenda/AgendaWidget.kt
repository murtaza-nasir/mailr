package io.github.murtazanasir.mailr.ui.agenda

import io.github.murtazanasir.mailr.util.catching

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.ColorFilter
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.LocalContext
import androidx.glance.LocalSize
import androidx.glance.action.Action
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetManager
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.provideContent
import androidx.glance.appwidget.updateAll
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.appwidget.lazy.LazyColumn
import androidx.glance.appwidget.lazy.itemsIndexed
import androidx.glance.layout.Box
import androidx.glance.layout.ColumnScope
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxHeight
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.semantics.contentDescription
import androidx.glance.semantics.semantics
import androidx.glance.text.FontFamily
import androidx.glance.text.FontStyle
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextAlign
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.weather.WeatherStore
import io.github.murtazanasir.mailr.data.weather.WeatherWork
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.ask.AskIntents
import io.github.murtazanasir.mailr.ui.today.TodayUndo
import io.github.murtazanasir.mailr.ui.today.TodayWidgets
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.ZonedDateTime

/**
 * The Agenda widget on the home screen (android.widget), in
 * one of four styles chosen per widget (AgendaStyle): glass timeline, paper & rail, week strip and
 * numeral duotone. All four show today's and the next days' events, tasks with a circle, and the
 * weather. Every element that does something has its own target: an event opens the event, a
 * circle completes the task (with Undo), a task's title opens the task, a day heading or a day of
 * the week strip opens that day, the weather opens the chosen weather app. The date header is the
 * one hidden target: it opens Ambrose already listening ("Talk to Ambrose" for TalkBack); there is
 * no microphone or button for it. It reads the phone's copy of the calendar and tasks, so it works
 * offline, and the weather comes from the last reading kept on the phone (WeatherWork).
 *
 * ACT calendar.today_view @android.widget, ACT task.complete @android.widget, ACT task.reopen @android.widget,
 * ACT agent.dictate @android.widget
 */
class AgendaWidget : GlanceAppWidget() {
    override val sizeMode: SizeMode = SizeMode.Exact

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val widgetId = catching { GlanceAppWidgetManager(context).getAppWidgetId(id) }.getOrDefault(AppWidgetManager.INVALID_APPWIDGET_ID)
        val styles = AgendaStyles(context)
        val style = styles[widgetId]
        val columns = styles.columns(widgetId)
        val model = AgendaWidgets.model(context)
        AgendaTicks.request(context, AgendaTicks.nextChange(style, model, AppClock.millis()))
        provideContent { AgendaWidgetContent(model, style, widgetId, columns) }
    }
}

class AgendaWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = AgendaWidget()

    /** The first Agenda widget placed: hourly weather starts. */
    override fun onEnabled(context: Context) {
        super.onEnabled(context)
        WeatherWork.schedule(context)
        WeatherWork.soon(context)
    }

    /** Widgets placed before styles existed keep the default, written down, so a later default never changes them. */
    override fun onUpdate(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray) {
        AgendaStyles(context).migrate(appWidgetIds)
        super.onUpdate(context, appWidgetManager, appWidgetIds)
    }

    override fun onDeleted(context: Context, appWidgetIds: IntArray) {
        super.onDeleted(context, appWidgetIds)
        val styles = AgendaStyles(context)
        appWidgetIds.forEach { styles.remove(it) }
    }

    /** The last one removed: no more weather fetches and no more ticks. */
    override fun onDisabled(context: Context) {
        super.onDisabled(context)
        WeatherWork.cancel(context)
        AgendaTicks.cancel(context)
    }
}

object AgendaWidgets {
    fun graph(context: Context): AppGraph? = TodayWidgets.graph(context)

    /** Every Agenda widget drawn again (a calendar or task change, a new weather reading, new settings, a tick). */
    suspend fun refresh(context: Context) {
        try {
            AgendaWidget().updateAll(context)
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            // No widget placed, or the launcher is away: nothing to draw.
        }
    }

    suspend fun model(context: Context, now: ZonedDateTime = AppClock.now()): AgendaModel {
        val store = WeatherStore(context)
        val hint = store.enabled && store.place == null
        return model(graph(context), now, android.text.format.DateFormat.is24HourFormat(context), store.days, store.shown(AppClock.millis()), hint)
    }

    suspend fun model(g: AppGraph?, now: ZonedDateTime, h24: Boolean, days: Int = AgendaModel.DEFAULT_DAYS,
                      weather: AgendaModel.Weather? = null, weatherHint: Boolean = false): AgendaModel {
        if (g == null || g.sessions.session.value == null) return AgendaModel.signedOut(now, weather, weatherHint)
        val today = now.toLocalDate()
        val from = AgendaModel.from(today).atStartOfDay(now.zone).toInstant().toEpochMilli()
        val to = AgendaModel.until(today, days).atStartOfDay(now.zone).toInstant().toEpochMilli()
        val hidden = g.calendar.collections.value.filterNot { it.visible }.map { it.url }.toSet()
        val events = catching { g.calendar.eventsNow(from, to) }.getOrDefault(emptyList()).filter { it.calendar !in hidden }
        val tasks = catching { g.calendar.tasksNow() }.getOrDefault(emptyList()).filter { it.list !in hidden }
        return AgendaModel.build(events, tasks, now, days, h24, weather, TodayUndo.get(), weatherHint)
    }

    fun eventUri(e: AgendaModel.Event): String =
        Uri.parse("mailr://event").buildUpon().appendPath(e.uid).appendQueryParameter("occ", e.occurrence).build().toString()

    fun dayUri(d: LocalDate): String = "mailr://calendar/day/$d"

    fun listen(context: Context): Intent = AskIntents.capture(context, null, listen = true, from = "agenda_widget")

    /** The widget's settings for widget [id] ("Set weather" in the header). The data keeps each widget's intent apart. */
    fun settings(context: Context, id: Int): Intent = Intent(context, AgendaSettingsActivity::class.java)
        .putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, id).setData(Uri.parse("mailr-agenda://settings/$id"))
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
}

class CompleteAgendaTaskAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val g = AgendaWidgets.graph(context) ?: return
        TodayWidgets.complete(g, parameters[KEY_AGENDA_TASK] ?: return)
        AgendaWidget().update(context, glanceId)
        g.scope.launch { delay(TodayUndo.UNDO_MS + 300); AgendaWidgets.refresh(context); TodayWidgets.refresh(context) }
    }
}

class UndoAgendaTaskAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val g = AgendaWidgets.graph(context) ?: return
        TodayWidgets.undo(g)
        AgendaWidget().update(context, glanceId)
    }
}

val KEY_AGENDA_TASK = ActionParameters.Key<String>("agenda_task")

// ============================ the face ============================

/**
 * The widget's face for a model in [style], at the widget's size (LocalSize). Tests render it directly.
 * [columns]: "Line up days and times" (AgendaColumns), on by default.
 */
@Composable
fun AgendaWidgetContent(m: AgendaModel, style: AgendaStyle = AgendaStyle.DEFAULT, widgetId: Int = AppWidgetManager.INVALID_APPWIDGET_ID,
                        columns: Boolean = true) {
    val size = AgendaSize.of(LocalSize.current)
    when (style) {
        AgendaStyle.GLASS -> GlassAgenda(m, size, widgetId, columns)
        AgendaStyle.PAPER -> PaperAgenda(m, size, widgetId, columns)
        AgendaStyle.WEEK -> WeekAgenda(m, size, widgetId, columns)
        AgendaStyle.NUMERAL -> NumeralAgenda(m, size, widgetId, columns)
    }
}

// ============================ shared parts ============================

internal fun weatherImage(i: WeatherIcon): Int = when (i) {
    WeatherIcon.CLEAR_DAY -> R.drawable.ic_wx_clear_day
    WeatherIcon.CLEAR_NIGHT -> R.drawable.ic_wx_clear_night
    WeatherIcon.PARTLY_DAY -> R.drawable.ic_wx_partly_day
    WeatherIcon.PARTLY_NIGHT -> R.drawable.ic_wx_partly_night
    WeatherIcon.CLOUDY -> R.drawable.ic_wx_cloudy
    WeatherIcon.FOG -> R.drawable.ic_wx_fog
    WeatherIcon.RAIN -> R.drawable.ic_wx_rain
    WeatherIcon.SNOW -> R.drawable.ic_wx_snow
    WeatherIcon.STORM -> R.drawable.ic_wx_storm
}

/** One line of text in a style's type: [family] null is the system sans-serif. */
@Composable
internal fun Txt(text: String, size: Float, color: ColorProvider, modifier: GlanceModifier = GlanceModifier, weight: FontWeight? = null,
                 family: FontFamily? = null, italic: Boolean = false, align: TextAlign? = null, maxLines: Int = 1) {
    Text(text, modifier, TextStyle(color = color, fontSize = size.sp, fontWeight = weight, fontFamily = family,
        fontStyle = if (italic) FontStyle.Italic else null, textAlign = align), maxLines)
}

/** The light weight of the system sans-serif (large numbers). */
internal val LIGHT = FontFamily("sans-serif-light")

/** The hidden Talk to Ambrose target: the date header. TalkBack reads "Talk to Ambrose" and the date. */
@Composable
internal fun GlanceModifier.talk(m: AgendaModel): GlanceModifier {
    val ctx = LocalContext.current
    return clickable(actionStartActivity(AgendaWidgets.listen(ctx)))
        .semantics { contentDescription = AppText.get(R.string.agenda_talk_cd, io.github.murtazanasir.mailr.i18n.AssistantName.now(), m.weekday, m.dayNumber, m.month) }
}

/** The weather's target: the weather app chosen in the settings (through a private trampoline). */
@Composable
internal fun GlanceModifier.weatherTap(w: AgendaModel.Weather): GlanceModifier {
    val ctx = LocalContext.current
    return clickable(actionStartActivity(Intent(ctx, WeatherOpenActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)))
        .semantics { contentDescription = AppText.get(R.string.agenda_weather_cd, w.tempLine,
            AppText.get(R.string.agenda_degrees, w.high), AppText.get(R.string.agenda_degrees, w.low)) }
}

/**
 * Where the weather goes in a header: Undo while a task was just completed, then the weather (drawn by
 * [content]), or "Set weather" while weather is on and no place is known (opens these settings).
 */
@Composable
internal fun WeatherSpot(m: AgendaModel, widgetId: Int, hintSize: Float = 12f, content: @Composable (AgendaModel.Weather) -> Unit) {
    val ctx = LocalContext.current
    m.undo?.let { UndoChip(it) }
    val w = m.weather
    when {
        w != null -> content(w)
        m.weatherHint -> Txt(AppText.get(R.string.agenda_set_weather), hintSize, AgendaInk.accent,
            GlanceModifier.padding(horizontal = 8.dp, vertical = 6.dp).clickable(actionStartActivity(AgendaWidgets.settings(ctx, widgetId))),
            weight = FontWeight.Medium)
    }
}

@Composable
internal fun UndoChip(u: io.github.murtazanasir.mailr.ui.today.TodayModel.Undo) {
    Box(GlanceModifier.padding(end = 6.dp)) {
        Txt(AppText.get(R.string.today_undo), 13f, AgendaInk.onAccent,
            GlanceModifier.background(ImageProvider(R.drawable.widget_chip), colorFilter = ColorFilter.tint(AgendaInk.accent))
                .padding(horizontal = 12.dp, vertical = 6.dp).clickable(actionRunCallback<UndoAgendaTaskAction>())
                .semantics { contentDescription = AppText.get(R.string.today_undo_cd, u.title) },
            weight = FontWeight.Medium)
    }
}

@Composable
internal fun WeatherIconImage(i: WeatherIcon, size: Dp) {
    Image(ImageProvider(weatherImage(i)), null, GlanceModifier.size(size))
}

@Composable
internal fun openEvent(e: AgendaModel.Event): Action = actionStartActivity(TodayWidgets.open(LocalContext.current, AgendaWidgets.eventUri(e)))

@Composable
internal fun openDay(d: LocalDate): Action = actionStartActivity(TodayWidgets.open(LocalContext.current, AgendaWidgets.dayUri(d)))

internal fun GlanceModifier.eventTarget(e: AgendaModel.Event, open: Action): GlanceModifier =
    clickable(open).semantics { contentDescription = AppText.get(R.string.today_event_cd, if (e.now) AppText.get(R.string.today_now) else e.time, e.title) }

internal fun GlanceModifier.dayTarget(label: String, open: Action): GlanceModifier =
    clickable(open).semantics { contentDescription = AppText.get(R.string.agenda_day_cd, label) }

/** A task's second line: "Overdue · Mon", its day or time, "Today", or "No date". */
internal fun taskNote(t: AgendaModel.Task): String = when {
    t.overdue -> t.note?.let { AppText.get(R.string.agenda_overdue_note, it) } ?: AppText.get(R.string.agenda_overdue)
    t.note != null -> t.note
    t.day != null -> AppText.get(R.string.agenda_today)
    else -> AppText.get(R.string.agenda_no_date)
}

/**
 * A task row: the circle (completes the task; the whole height of the row is its target) and the
 * title (opens the task), with its note under it ("Overdue · Mon", "Sat", "20:00").
 */
@Composable
internal fun TaskRow(t: AgendaModel.Task, height: Float, circle: Float = 16f, fs: Float = 12.5f) {
    val ctx = LocalContext.current
    Row(GlanceModifier.fillMaxWidth().height(height.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(GlanceModifier.width((circle + 10).dp).fillMaxHeight()
            .clickable(actionRunCallback<CompleteAgendaTaskAction>(actionParametersOf(KEY_AGENDA_TASK to t.uid)))
            .semantics { contentDescription = AppText.get(R.string.today_complete_cd, t.title) }, contentAlignment = Alignment.Center) {
            Image(ImageProvider(R.drawable.agenda_ring), null, GlanceModifier.size(circle.dp),
                colorFilter = ColorFilter.tint(if (t.overdue) AgendaInk.danger else AgendaInk.accent))
        }
        Column(GlanceModifier.defaultWeight().fillMaxHeight().padding(start = 4.dp)
            .clickable(actionStartActivity(TodayWidgets.open(ctx, TodayWidgets.taskUri(t.uid)))),
            verticalAlignment = Alignment.CenterVertically) {
            Txt(t.title, fs, AgendaInk.text)
            Txt(taskNote(t), 10.5f, if (t.overdue) AgendaInk.danger else AgendaInk.text2,
                weight = if (t.overdue) FontWeight.Medium else null)
        }
    }
}

/** "Tasks 5": the tasks column's heading. */
@Composable
internal fun TasksHeading(count: Int, height: Float, label: String = AppText.get(R.string.agenda_tasks), serif: Boolean = false) {
    Row(GlanceModifier.fillMaxWidth().height(height.dp), verticalAlignment = Alignment.CenterVertically) {
        if (serif) Txt(label, 12f, AgendaInk.accent, family = FontFamily.Serif, italic = true)
        else Txt(label, if (height < 18) 10.5f else 11f, AgendaInk.accent, weight = FontWeight.Medium)
        Spacer(GlanceModifier.width(6.dp))
        Txt(count.toString(), 10f, AgendaInk.text3)
    }
}

/**
 * The tasks column: the heading, then every task in a list that takes the rest of the column's
 * height and scrolls when the tasks do not fit; "No tasks" when there are none.
 */
@Composable
internal fun ColumnScope.TasksColumn(m: AgendaModel, headH: Float, rowH: Float, circle: Float = 16f, fs: Float = 12.5f,
                                     label: String = AppText.get(R.string.agenda_tasks), serif: Boolean = false) {
    if (!m.ready) return
    TasksHeading(m.tasks.size, headH, label, serif)
    if (m.tasks.isEmpty()) {
        Quiet(AppText.get(R.string.agenda_no_tasks))
        return
    }
    ScrollList(m.tasks) { TaskRow(it, rowH, circle, fs) }
}

/**
 * [rows] in a list that fills the rest of the column (a Glance LazyColumn, a scrolling list on the
 * home screen). Rows keep their own height, so a few rows end where they end; more than fit scroll.
 */
@Composable
internal fun <T> ColumnScope.ScrollList(rows: List<T>, row: @Composable (T) -> Unit) {
    LazyColumn(GlanceModifier.fillMaxWidth().defaultWeight()) {
        itemsIndexed(rows, itemId = { i, _ -> i.toLong() }) { _, r -> row(r) }
    }
}

/** A quiet line in place of an empty list, or the signed-out note. */
@Composable
internal fun Quiet(text: String, modifier: GlanceModifier = GlanceModifier) {
    Txt(text, 12f, AgendaInk.text2, modifier.padding(top = 4.dp), maxLines = 2)
}

/** What the events side says when it has nothing: signed out, or nothing planned. */
internal fun emptyEvents(m: AgendaModel): String =
    AppText.get(if (!m.ready) R.string.agenda_sign_in else R.string.agenda_empty)

/** A hairline, horizontal ([vertical] false) or vertical. */
@Composable
internal fun Hairline(color: ColorProvider = AgendaInk.line, vertical: Boolean = false, modifier: GlanceModifier = GlanceModifier) {
    Box((if (vertical) modifier.width(1.dp).fillMaxHeight() else modifier.fillMaxWidth().height(1.dp)).background(color)) {}
}
