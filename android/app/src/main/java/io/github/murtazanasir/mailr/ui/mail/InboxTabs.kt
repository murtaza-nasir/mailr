package io.github.murtazanasir.mailr.ui.mail

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import android.os.SystemClock
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.interaction.DragInteraction
import androidx.compose.foundation.rememberScrollState
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Badge
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.PrimaryScrollableTabRow
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.murtazanasir.mailr.data.api.InboxLayout
import io.github.murtazanasir.mailr.data.api.LayoutTab
import io.github.murtazanasir.mailr.data.api.TabCounts
import io.github.murtazanasir.mailr.data.repo.MailView
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats

/**
 * The inbox layout's tabs across the top of the inbox, as on the web: icon,
 * name and the tab's indicators (unread "3 new", all "120", due "Due Oct 4 · 2"), the most
 * important first. A wide screen (the unfolded fold) has room for two pills; a phone shows one
 * compact indicator: a small number, or a coloured dot for a deadline. Only when the layout
 * has more than one tab. The row scrolls sideways when the tabs do not fit.
 *
 * [counts] is the older one-number-per-tab answer, used for a tab missing from [indicators]
 * (an older server).
 */
@Composable
fun InboxTabs(
    layout: InboxLayout,
    current: MailView,
    counts: Map<String, Int>,
    indicators: Map<String, TabCounts> = emptyMap(),
    onOpen: (MailView) -> Unit,
) {
    val tabs = layout.tabs
    val selected = tabs.indexOfFirst { it.id == current.tab }.coerceAtLeast(0)
    val wide = LocalConfiguration.current.screenWidthDp >= 600
    val today = AppClock.today()
    val scroll = rememberScrollState()
    val userScroll = rememberUserScroll(scroll)
    // The row's own divider ends with the last tab; a full-width one sits under it instead.
    Column {
    PrimaryScrollableTabRow(selectedTabIndex = selected, scrollState = scroll, edgePadding = 8.dp,
        containerColor = MaterialTheme.colorScheme.surface, divider = {}) {
        tabs.forEachIndexed { i, t ->
            val c = indicators[t.id] ?: legacyCounts(t, counts[t.id])
            val shown = tabIndicators(t, c, today).take(if (wide) 2 else 1)
            val open = { if (i != selected) onOpen(MailView("inbox", title = t.name, layout = layout.id, tab = t.id)) }
            Tab(
                selected = i == selected,
                onClick = open,
                modifier = Modifier
                    .tapWhileTheRowCentres(scroll, userScroll, open)
                    .semantics {
                        contentDescription = (listOf(t.name) + shown.map { it.title }).joinToString(", ")
                    },
                text = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(tabIcon(t.icon), null, Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text(t.name, maxLines = 1)
                        shown.forEach { ind ->
                            Spacer(Modifier.width(if (wide) 8.dp else 6.dp))
                            IndicatorMark(ind, compact = !wide)
                        }
                    }
                },
            )
        }
    }
    HorizontalDivider()
    }
}

/** The user's own drags of the row: one under way, and when the last one ended (uptime ms). */
private class UserScroll {
    var dragging = false
    var ended: Long? = null
}

@Composable
private fun rememberUserScroll(scroll: ScrollState): UserScroll {
    val u = remember { UserScroll() }
    LaunchedEffect(scroll) {
        scroll.interactionSource.interactions.collect { i ->
            u.dragging = i is DragInteraction.Start
            if (!u.dragging) u.ended = SystemClock.uptimeMillis()
        }
    }
    return u
}

/**
 * A tap on a tab while the row glides to centre the selected tab (about a quarter second after
 * the tab changes, from a tap or from the drawer) still opens the tab. The row is a scroller, and a
 * scroller takes a touch during its own scrolling as the start of a drag: the tap only stopped the
 * glide and the tab stayed as it was. A touch
 * during the user's own fling still just stops it.
 */
private fun Modifier.tapWhileTheRowCentres(scroll: ScrollState, user: UserScroll, onTap: () -> Unit): Modifier =
    pointerInput(scroll, user) {
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial)
            val now = SystemClock.uptimeMillis()
            val gliding = scroll.isScrollInProgress && !user.dragging && user.ended.let { it == null || now - it > 1_000 }
            if (!gliding) return@awaitEachGesture
            val slop = viewConfiguration.touchSlop
            while (true) {
                val e = awaitPointerEvent(PointerEventPass.Initial)
                val ch = e.changes.firstOrNull { it.id == down.id } ?: return@awaitEachGesture
                if ((ch.position - down.position).getDistance() > slop) return@awaitEachGesture
                if (!ch.pressed) {
                    val inside = ch.position.x in 0f..size.width.toFloat() && ch.position.y in 0f..size.height.toFloat()
                    if (inside) {
                        ch.consume()
                        onTap()
                    }
                    return@awaitEachGesture
                }
            }
        }
    }

@Composable
private fun IndicatorMark(ind: TabIndicator, compact: Boolean) {
    val cs = MaterialTheme.colorScheme
    if (compact && ind.kind == "due") {
        // A deadline on a phone: a dot in the Due chip's colour (the description has the words).
        val dot = if (ind.tone == Tone.Urgent) cs.error else cs.tertiary
        Box(Modifier.size(8.dp).background(dot, CircleShape).testTag("tab-due-dot"))
        return
    }
    if (compact) {
        val color = if (ind.tone == Tone.Accent) cs.primary else cs.onSurfaceVariant
        Text(ind.short, maxLines = 1, color = color, fontSize = 12.sp, style = MaterialTheme.typography.labelMedium)
        return
    }
    // The Due chip's colours for a deadline (ConversationRow), the accent for unread.
    val (bg, fg) = when (ind.tone) {
        Tone.Urgent -> cs.errorContainer to cs.onErrorContainer
        Tone.Warning -> cs.tertiaryContainer to cs.onTertiaryContainer
        Tone.Accent -> cs.primary to cs.onPrimary
        Tone.Neutral -> cs.surfaceVariant to cs.onSurfaceVariant
    }
    Badge(containerColor = bg, contentColor = fg) {
        Text(ind.text, maxLines = 1, modifier = Modifier.padding(horizontal = 2.dp))
    }
}

internal enum class Tone { Urgent, Warning, Accent, Neutral }

/** One indicator: the pill text, the compact text ("" for a dot), its tone and the full sentence. */
internal data class TabIndicator(val kind: String, val text: String, val short: String, val tone: Tone, val title: String)

private val SHORT_DAY: DateTimeFormatter get() = LocaleFormats.date("MMMd", "MMM d")
private val LONG_DAY: DateTimeFormatter get() = LocaleFormats.date("EEEdMMM", "EEE d MMM")

/** A tab's indicators; a tab from an older server has only its badge. */
internal fun indicatorsOf(t: LayoutTab): List<String> =
    t.indicators ?: if (t.badge == "unread" || t.badge == "all") listOf(t.badge) else emptyList()

private fun legacyCounts(t: LayoutTab, n: Int?): TabCounts? = when {
    n == null -> null
    t.badge == "unread" -> TabCounts(unread = n)
    t.badge == "all" -> TabCounts(all = n)
    else -> null
}

/**
 * The tab's indicators in order of importance, as on the web (lib/tabIndicators.ts): an urgent
 * deadline (within two days, or past), unread, a later deadline, all.
 */
internal fun tabIndicators(t: LayoutTab, c: TabCounts?, today: LocalDate): List<TabIndicator> {
    if (c == null) return emptyList()
    val want = indicatorsOf(t)
    val out = mutableListOf<Pair<Int, TabIndicator>>()
    val due = c.earliestDue?.let { catching { LocalDate.parse(it) }.getOrNull() }
    val dueN = c.dueCount ?: 0
    if ("due" in want && dueN > 0 && due != null) {
        val days = ChronoUnit.DAYS.between(today, due)
        val overdue = days < 0
        val urgent = days <= 2
        val more = if (dueN > 1) AppText.get(R.string.mail_tab_due_more, dueN) else ""
        val who = c.earliestDueFrom?.takeIf { it.isNotBlank() }?.let { AppText.get(R.string.mail_tab_due_from, it) } ?: ""
        out += (if (urgent) 0 else 2) to TabIndicator(
            "due", (if (overdue) AppText.get(R.string.mail_tab_overdue) else AppText.get(R.string.mail_tab_due, SHORT_DAY.format(due))) + more, "",
            if (urgent) Tone.Urgent else Tone.Warning,
            AppText.plural(R.plurals.mail_tab_due_title, dueN, dueN, LONG_DAY.format(due)) +
                (if (overdue) AppText.get(R.string.mail_tab_overdue_suffix) else "") + who,
        )
    }
    val unread = c.unread ?: 0
    if ("unread" in want && unread > 0) {
        val num = if (unread > 999) AppText.get(R.string.mail_count_capped) else "$unread"
        val label = AppText.get(R.string.mail_tab_new, num)
        out += 1 to TabIndicator("unread", label, num, Tone.Accent, label)
    }
    val all = c.all ?: 0
    if ("all" in want && all > 0) {
        val num = tabTotal(all)
        out += 3 to TabIndicator("all", num, num, Tone.Neutral, AppText.plural(R.plurals.mail_tab_conversations, all, num))
    }
    return out.sortedBy { it.first }.map { it.second }
}

/** The server stops counting a tab's conversations one past 200,000; above that the pill says
 *  "200,000+", as the list totals do. */
internal fun tabTotal(n: Int): String =
    if (n > 200_000) AppText.get(R.string.mail_tab_total_capped, LocaleFormats.number(200_000)) else LocaleFormats.number(n)
