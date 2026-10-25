package io.github.murtazanasir.mailr.ui.today

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.ColorFilter
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.GlanceTheme
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.LocalSize
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.provideContent
import androidx.glance.appwidget.updateAll
import androidx.glance.background
import androidx.glance.color.ColorProvider
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.semantics.contentDescription
import androidx.glance.semantics.semantics
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.MailrApp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.ui.MainActivity
import io.github.murtazanasir.mailr.ui.ask.AskIntents
import io.github.murtazanasir.mailr.ui.ask.AskWidgetColors
import io.github.murtazanasir.mailr.ui.calendar.CalendarColors
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.ZonedDateTime
import java.util.concurrent.ConcurrentHashMap

/**
 * The Today widget on the home screen (android.widget):
 * a greeting for the part of the day with a small picture, today's events that are still ahead,
 * the tasks due today or overdue with a tick, and a "Talk to Ambrose" button in the bottom right
 * corner that opens Ambrose already listening. It reads the phone's copy of the calendar and the
 * tasks, so it works offline; a tick goes through the replay queue, as a reminder's Done does, and
 * the header offers Undo for 7 s. It is redrawn after every calendar or task change on the phone
 * (MailrApp) and every 30 minutes, so the greeting and the next event keep up.
 *
 * ACT task.complete @android.widget, ACT agent.dictate @android.widget, ACT calendar.today_view @android.widget
 */
class TodayWidget : GlanceAppWidget() {
    override val sizeMode: SizeMode = SizeMode.Exact

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val model = TodayWidgets.model(context)
        provideContent { TodayWidgetContent(model) }
    }
}

class TodayWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = TodayWidget()
}

/** The last tick made in a Today widget, for the header's 7 s Undo chip. */
object TodayUndo {
    const val UNDO_MS = 7_000L
    private val last = ConcurrentHashMap<String, Pair<TodayModel.Undo, Long>>()
    fun put(u: TodayModel.Undo, now: Long = AppClock.millis()) { last.clear(); last["t"] = u to now }
    fun get(now: Long = AppClock.millis()): TodayModel.Undo? = last["t"]?.takeIf { now - it.second < UNDO_MS }?.first
    fun clear() = last.clear()
}

object TodayWidgets {
    fun graph(context: Context): AppGraph? = (context.applicationContext as? MailrApp)?.graph

    /** Every Today widget drawn again (after a calendar or task change on the phone). */
    suspend fun refresh(context: Context) {
        try {
            TodayWidget().updateAll(context)
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            // No widget placed, or the launcher is away: nothing to draw.
        }
    }

    suspend fun model(context: Context, now: ZonedDateTime = AppClock.now()): TodayModel =
        model(graph(context), now, android.text.format.DateFormat.is24HourFormat(context))

    suspend fun model(g: AppGraph?, now: ZonedDateTime, h24: Boolean): TodayModel {
        if (g == null || g.sessions.session.value == null) return TodayModel.signedOut(now)
        val day = now.toLocalDate()
        val from = day.atStartOfDay(now.zone).toInstant().toEpochMilli()
        val to = day.plusDays(1).atStartOfDay(now.zone).toInstant().toEpochMilli()
        val hidden = g.calendar.collections.value.filterNot { it.visible }.map { it.url }.toSet()
        val events = catching { g.calendar.eventsNow(from, to) }.getOrDefault(emptyList()).filter { it.calendar !in hidden }
        val tasks = catching { g.calendar.tasksNow() }.getOrDefault(emptyList()).filter { it.list !in hidden }
        return TodayModel.build(events, tasks, now, h24, TodayUndo.get())
    }

    /** ACT task.complete @android.widget: the tick, through the replay queue (offline too). */
    suspend fun complete(g: AppGraph, uid: String) {
        val title = g.calendar.task(uid)?.summary.orEmpty()
        g.calendar.setTaskDone(uid, true, viaQueue = true)
        TodayUndo.put(TodayModel.Undo(uid, title))
    }

    /** The header's Undo: the task is open again. */
    suspend fun undo(g: AppGraph) {
        val u = TodayUndo.get() ?: return
        TodayUndo.clear()
        g.calendar.setTaskDone(u.uid, false, viaQueue = true)
    }

    fun open(context: Context, uri: String): Intent =
        Intent(Intent.ACTION_VIEW, Uri.parse(uri), context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)

    fun eventUri(e: TodayModel.Event): String =
        Uri.parse("mailr://event").buildUpon().appendPath(e.uid).appendQueryParameter("occ", e.occurrence).build().toString()

    fun taskUri(uid: String): String = Uri.parse("mailr://task").buildUpon().appendPath(uid).build().toString()

    const val CALENDAR_URI = "mailr://calendar/day"
    const val TASKS_URI = "mailr://tasks/today"
}

val KEY_TASK = ActionParameters.Key<String>("task")

class CompleteTaskAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val g = TodayWidgets.graph(context) ?: return
        TodayWidgets.complete(g, parameters[KEY_TASK] ?: return)
        TodayWidget().update(context, glanceId)
        // The Undo chip goes when its 7 s are up.
        g.scope.launch { delay(TodayUndo.UNDO_MS + 300); TodayWidgets.refresh(context) }
    }
}

class UndoCompleteAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val g = TodayWidgets.graph(context) ?: return
        TodayWidgets.undo(g)
        TodayWidget().update(context, glanceId)
    }
}

private fun dayPartImage(p: DayPart): Int = when (p) {
    DayPart.MORNING -> R.drawable.ic_daypart_morning
    DayPart.AFTERNOON -> R.drawable.ic_daypart_afternoon
    DayPart.EVENING -> R.drawable.ic_daypart_evening
    DayPart.NIGHT -> R.drawable.ic_daypart_night
}

/** The widget's face for a model, at the widget's size (LocalSize). Tests render it directly. */
@Composable
fun TodayWidgetContent(m: TodayModel) {
    val ctx = androidx.glance.LocalContext.current
    val size = LocalSize.current
    val compact = size.height < 150.dp
    val narrow = size.width < 220.dp
    GlanceTheme(AskWidgetColors) {
        Column(GlanceModifier.fillMaxSize().background(ImageProvider(R.drawable.widget_bg), colorFilter = ColorFilter.tint(GlanceTheme.colors.surface)).padding(12.dp)) {
            if (compact) {
                Row(GlanceModifier.fillMaxSize(), verticalAlignment = Alignment.CenterVertically) {
                    Column(GlanceModifier.defaultWeight()) {
                        Header(m, narrow, small = true)
                        Spacer(GlanceModifier.height(6.dp))
                        NextLine(m)
                    }
                    Spacer(GlanceModifier.width(8.dp))
                    TalkButton(ctx, round = true)
                }
            } else {
                Header(m, narrow, small = false)
                Spacer(GlanceModifier.height(10.dp))
                Column(GlanceModifier.defaultWeight().fillMaxWidth()) { Body(m, size) }
                Row(GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    if (m.ready && !m.empty && !narrow) {
                        val shown = shownRows(m, size)
                        val more = m.events.size + m.tasks.size - shown.first - shown.second
                        val tasksLeft = m.tasks.size > shown.second
                        Text(if (more > 0) AppText.plural(R.plurals.today_more, more, more) else AppText.get(R.string.today_open_calendar),
                            style = TextStyle(color = GlanceTheme.colors.primary, fontSize = 13.sp, fontWeight = FontWeight.Medium),
                            modifier = GlanceModifier.padding(vertical = 8.dp, horizontal = 4.dp)
                                .clickable(actionStartActivity(TodayWidgets.open(ctx, if (tasksLeft) TodayWidgets.TASKS_URI else TodayWidgets.CALENDAR_URI))))
                    }
                    Spacer(GlanceModifier.defaultWeight())
                    TalkButton(ctx, round = narrow)
                }
            }
        }
    }
}

@Composable
private fun Header(m: TodayModel, narrow: Boolean, small: Boolean) {
    val ctx = androidx.glance.LocalContext.current
    Row(GlanceModifier.fillMaxWidth().clickable(actionStartActivity(TodayWidgets.open(ctx, TodayWidgets.CALENDAR_URI)))
        .semantics { contentDescription = AppText.get(R.string.today_header_cd, m.part.greeting, m.dateLine, m.summary) },
        verticalAlignment = Alignment.CenterVertically) {
        Image(ImageProvider(dayPartImage(m.part)), null, GlanceModifier.size(if (small) 36.dp else 44.dp))
        Spacer(GlanceModifier.width(10.dp))
        Column(GlanceModifier.defaultWeight()) {
            Text(m.part.greeting, maxLines = 1,
                style = TextStyle(color = GlanceTheme.colors.onSurface, fontSize = if (small) 16.sp else 18.sp, fontWeight = FontWeight.Bold))
            Text(if (narrow || !m.ready) m.dateLine else AppText.get(R.string.today_date_summary, m.dateLine, m.summary), maxLines = 1,
                style = TextStyle(color = GlanceTheme.colors.onSurfaceVariant, fontSize = 12.sp))
        }
        m.undo?.let {
            Spacer(GlanceModifier.width(6.dp))
            Text(AppText.get(R.string.today_undo), style = TextStyle(color = GlanceTheme.colors.onPrimaryContainer, fontWeight = FontWeight.Medium, fontSize = 13.sp),
                modifier = GlanceModifier.background(ImageProvider(R.drawable.widget_chip), colorFilter = ColorFilter.tint(GlanceTheme.colors.primaryContainer))
                    .padding(horizontal = 12.dp, vertical = 6.dp).clickable(actionRunCallback<UndoCompleteAction>())
                    .semantics { contentDescription = AppText.get(R.string.today_undo_cd, it.title) })
        }
    }
}

/** The compact widget's one line: the next event, else the first task, else the calm empty line. */
@Composable
private fun NextLine(m: TodayModel) {
    val ctx = androidx.glance.LocalContext.current
    val e = m.events.firstOrNull()
    val t = m.tasks.firstOrNull()
    val style = TextStyle(color = GlanceTheme.colors.onSurface, fontSize = 13.sp)
    when {
        !m.ready -> Text(AppText.get(R.string.today_sign_in), maxLines = 1, style = style)
        e != null -> Text(AppText.get(R.string.today_next_line, if (e.now) AppText.get(R.string.today_now) else e.time, e.title), maxLines = 1, style = style,
            modifier = GlanceModifier.clickable(actionStartActivity(TodayWidgets.open(ctx, TodayWidgets.eventUri(e)))))
        t != null -> Text(if (t.overdue) AppText.get(R.string.today_next_overdue, t.title) else AppText.get(R.string.today_next_due, t.title), maxLines = 1, style = style,
            modifier = GlanceModifier.clickable(actionStartActivity(TodayWidgets.open(ctx, TodayWidgets.taskUri(t.uid)))))
        else -> Text(TodayModel.EMPTY, maxLines = 1, style = TextStyle(color = GlanceTheme.colors.onSurfaceVariant, fontSize = 13.sp))
    }
}

@Composable
private fun Body(m: TodayModel, size: DpSize) {
    if (!m.ready) {
        Text(AppText.get(R.string.today_sign_in), style = TextStyle(color = GlanceTheme.colors.onSurfaceVariant, fontSize = 14.sp))
        return
    }
    if (m.empty) {
        Box(GlanceModifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text(TodayModel.EMPTY, style = TextStyle(color = GlanceTheme.colors.onSurfaceVariant, fontSize = 15.sp))
        }
        return
    }
    val (ne, nt) = shownRows(m, size)
    m.events.take(ne).forEach { EventRow(it) }
    if (ne > 0 && nt > 0) Spacer(GlanceModifier.height(4.dp))
    m.tasks.take(nt).forEach { TaskRow(it) }
}

/**
 * How many events and tasks fit between the header (about 56 dp) and the bottom bar (about
 * 52 dp), 40 dp a row: events first, but at most half the rows while tasks wait.
 */
fun shownRows(m: TodayModel, size: DpSize): Pair<Int, Int> {
    val fit = ((size.height.value - 120f) / 40f).toInt().coerceAtLeast(1)
    val ne = if (m.tasks.isEmpty()) minOf(fit, m.events.size) else minOf(m.events.size, maxOf(1, fit / 2))
    return ne to minOf(m.tasks.size, (fit - ne).coerceAtLeast(0))
}

@Composable
private fun EventRow(e: TodayModel.Event) {
    val ctx = androidx.glance.LocalContext.current
    val c = CalendarColors.of(e.calendar, e.colour)
    Row(GlanceModifier.fillMaxWidth().height(40.dp).clickable(actionStartActivity(TodayWidgets.open(ctx, TodayWidgets.eventUri(e))))
        .semantics { contentDescription = AppText.get(R.string.today_event_cd, if (e.now) AppText.get(R.string.today_now) else e.time, e.title) },
        verticalAlignment = Alignment.CenterVertically) {
        Box(GlanceModifier.width(22.dp), contentAlignment = Alignment.Center) {
            Box(GlanceModifier.width(4.dp).height(28.dp).background(ImageProvider(R.drawable.widget_bar), colorFilter = ColorFilter.tint(ColorProvider(c, c)))) {}
        }
        Spacer(GlanceModifier.width(10.dp))
        Column(GlanceModifier.defaultWeight()) {
            Text(if (e.now) AppText.get(R.string.today_now) else e.time, maxLines = 1, style = TextStyle(
                color = if (e.now) GlanceTheme.colors.tertiary else GlanceTheme.colors.primary, fontSize = 12.sp, fontWeight = FontWeight.Medium))
            Text(e.title, maxLines = 1, style = TextStyle(color = GlanceTheme.colors.onSurface, fontSize = 14.sp))
        }
    }
}

@Composable
private fun TaskRow(t: TodayModel.Task) {
    val ctx = androidx.glance.LocalContext.current
    Row(GlanceModifier.fillMaxWidth().height(40.dp), verticalAlignment = Alignment.CenterVertically) {
        // The tick: a 40 dp target round the circle; TalkBack reads "Complete …".
        Box(GlanceModifier.size(40.dp)
            .clickable(actionRunCallback<CompleteTaskAction>(actionParametersOf(KEY_TASK to t.uid)))
            .semantics { contentDescription = AppText.get(R.string.today_complete_cd, t.title) }, contentAlignment = Alignment.Center) {
            Image(ImageProvider(R.drawable.ic_task_open), null, GlanceModifier.size(22.dp),
                colorFilter = ColorFilter.tint(if (t.overdue) GlanceTheme.colors.error else GlanceTheme.colors.primary))
        }
        Column(GlanceModifier.defaultWeight().clickable(actionStartActivity(TodayWidgets.open(ctx, TodayWidgets.taskUri(t.uid))))) {
            Text(t.title, maxLines = 1, style = TextStyle(color = GlanceTheme.colors.onSurface, fontSize = 14.sp))
            t.note?.let {
                Text(it, maxLines = 1, style = TextStyle(color = if (t.overdue) GlanceTheme.colors.error else GlanceTheme.colors.onSurfaceVariant,
                    fontSize = 12.sp))
            }
        }
    }
}

/**
 * "Talk to Ambrose": one tap opens Ambrose listening (no further tap). [round]: the 48 dp
 * microphone alone, for narrow sizes; otherwise a pill with the words.
 */
@Composable
fun TalkButton(ctx: Context, round: Boolean) {
    val listen = actionStartActivity(AskIntents.capture(ctx, null, listen = true, from = "today_widget"))
    if (round) {
        Box(GlanceModifier.size(48.dp).background(ImageProvider(R.drawable.widget_circle), colorFilter = ColorFilter.tint(GlanceTheme.colors.primary)).clickable(listen)
            .semantics { contentDescription = AppText.get(R.string.today_talk, io.github.murtazanasir.mailr.i18n.AssistantName.now()) }, contentAlignment = Alignment.Center) {
            Image(ImageProvider(R.drawable.ic_ask_mic), null, GlanceModifier.size(24.dp), colorFilter = ColorFilter.tint(GlanceTheme.colors.onPrimary))
        }
    } else {
        Row(GlanceModifier.height(44.dp).background(ImageProvider(R.drawable.widget_pill), colorFilter = ColorFilter.tint(GlanceTheme.colors.primary)).clickable(listen)
            .padding(start = 14.dp, end = 18.dp).semantics { contentDescription = AppText.get(R.string.today_talk, io.github.murtazanasir.mailr.i18n.AssistantName.now()) },
            verticalAlignment = Alignment.CenterVertically) {
            Image(ImageProvider(R.drawable.ic_ask_mic), null, GlanceModifier.size(20.dp), colorFilter = ColorFilter.tint(GlanceTheme.colors.onPrimary))
            Spacer(GlanceModifier.width(8.dp))
            Text(AppText.get(R.string.today_talk, io.github.murtazanasir.mailr.i18n.AssistantName.now()), maxLines = 1, style = TextStyle(color = GlanceTheme.colors.onPrimary, fontSize = 14.sp, fontWeight = FontWeight.Medium))
        }
    }
}
