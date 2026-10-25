package io.github.murtazanasir.mailr.ui.calendar

import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CalendarMonth
import io.github.murtazanasir.mailr.look.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import io.github.murtazanasir.mailr.look.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.reminders.SpanUnit
import io.github.murtazanasir.mailr.data.reminders.WhenSheet
import java.time.ZonedDateTime

/** The phone's horizontal gutter for the sheet (8 dp grid). */
internal val SHEET_PAD = 16.dp

/** The last base the user chose for a verb on this device (From now or from the due or start: the last choice per verb is remembered). */
internal object WhenSheetPrefs {
    private fun prefs(c: Context) = c.getSharedPreferences("when_sheet", Context.MODE_PRIVATE)
    fun base(c: Context, s: WhenSheet.Spec): WhenSheet.Base {
        if (!WhenSheet.hasBaseToggle(s)) return WhenSheet.Base.NOW
        return when (prefs(c).getString("base.${s.verb.name}", null)) {
            "NOW" -> WhenSheet.Base.NOW
            "DUE" -> WhenSheet.Base.DUE
            else -> WhenSheet.defaultBase(s)
        }
    }
    fun remember(c: Context, verb: WhenSheet.Verb, b: WhenSheet.Base) = prefs(c).edit().putString("base.${verb.name}", b.name).apply()
}

/** A small title above a group of tiles ("Later", "Same time", "Custom"). */
@Composable
internal fun SheetLabel(text: String, modifier: Modifier = Modifier) {
    Text(text, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.SemiBold,
        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = modifier.semantics { heading() })
}

/**
 * One preset: its label and, on a smaller second line, the moment it resolves to. One tap
 * confirms. Compact: 48 dp high, the touch target; TalkBack reads both lines as one button.
 */
@Composable
private fun PresetTile(tile: WhenSheet.Tile, now: ZonedDateTime, modifier: Modifier, onClick: () -> Unit) {
    val time = WhenSheet.tileTime(tile, now)
    Surface(onClick = onClick, modifier = modifier.heightIn(min = 48.dp).testTag("tile-${tile.id}"), shape = RoundedCornerShape(10.dp),
        color = MaterialTheme.colorScheme.surfaceContainerHighest) {
        Column(Modifier.padding(horizontal = 12.dp, vertical = 5.dp), verticalArrangement = Arrangement.Center) {
            Text(tile.label, style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(time, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** Equal tiles in a grid: two columns on phones, three from 560 dp. */
@Composable
private fun TileGrid(tiles: List<WhenSheet.Tile>, now: ZonedDateTime, onPick: (WhenSheet.Tile) -> Unit) {
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        val cols = if (maxWidth >= 560.dp) 3 else 2
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            tiles.chunked(cols).forEach { row ->
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    row.forEach { t -> PresetTile(t, now, Modifier.weight(1f)) { onPick(t) } }
                    repeat(cols - row.size) { Spacer(Modifier.weight(1f)) }
                }
            }
        }
    }
}

/**
 * Two compact wheels, amount and unit, in one rounded container with one selection band across
 * both: three rows show, 108 dp in all.
 */
@Composable
internal fun CompactDurationWheels(amount: Int, unit: SpanUnit, before: Boolean, onChange: (Int, SpanUnit) -> Unit) {
    val units = SpanUnit.entries
    Box(Modifier.fillMaxWidth().height(COMPACT_WHEEL_ROW * 3).background(MaterialTheme.colorScheme.surfaceContainerHighest, RoundedCornerShape(14.dp))) {
        Box(Modifier.fillMaxWidth().padding(horizontal = 8.dp).height(COMPACT_WHEEL_ROW).align(Alignment.Center)
            .background(MaterialTheme.colorScheme.secondaryContainer, RoundedCornerShape(10.dp)))
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp)) {
            // A new wheel per unit: its list differs, so its scroll position starts at the kept amount.
            key(unit) {
                WheelPicker(unit.amounts.map { it.toString() }, unit.amounts.indexOf(amount).coerceAtLeast(0),
                    { onChange(unit.amounts[it], unit) }, Modifier.weight(1f).testTag("wheel-amount"), label = stringResource(R.string.cal_wheel_amount), visible = 3, band = false, row = COMPACT_WHEEL_ROW)
            }
            val beforeFmt = stringResource(R.string.cal_wheel_unit_before)
            WheelPicker(units.map { if (before) beforeFmt.format(it.label) else it.label }, units.indexOf(unit), { i ->
                onChange(units[i].nearest(amount), units[i])
            }, Modifier.weight(1.6f).testTag("wheel-unit"), label = stringResource(R.string.cal_wheel_unit), visible = 3, band = false, row = COMPACT_WHEEL_ROW)
        }
    }
}

/**
 * The when sheet: what Snooze, Reschedule and Remind
 * before expand into. A line on what the verb changes, preset tiles in titled groups, the custom
 * block (From now | From due time, compact wheels, live result), and a footer with the verb and
 * "Pick date & time…". [onPick] gets the moment, whether it is date-only, and the tile when one
 * was tapped. [onFull] opens the full day/hour/minute picker; null leaves the button out.
 */
@Composable
fun WhenSheetPanel(spec: WhenSheet.Spec, confirm: String, anchorText: String?, modifier: Modifier = Modifier,
                   scrollable: Boolean = true, showHint: Boolean = true, onFull: (() -> Unit)?, onPick: (ZonedDateTime, Boolean, WhenSheet.Tile?) -> Unit) {
    val context = LocalContext.current
    val groups = remember(spec) { WhenSheet.groups(spec) }
    val toggle = WhenSheet.hasBaseToggle(spec)
    var base by remember(spec.verb) { mutableStateOf(WhenSheetPrefs.base(context, spec)) }
    val start = WhenSheet.initial(spec.verb)
    var amount by remember(spec.verb) { mutableIntStateOf(start.first) }
    var unit by remember(spec.verb) { mutableStateOf(start.second) }
    val (result, resultDateOnly) = WhenSheet.custom(spec, base, amount, unit)
    val past = WhenSheet.past(result, resultDateOnly, spec.now)
    // A snooze and a reminder need a moment ahead; a reschedule may land in the past (the task is then overdue).
    val blocked = past && spec.verb != WhenSheet.Verb.RESCHEDULE

    Column(modifier.fillMaxWidth()) {
        // Inside a sheet that scrolls already ([scrollable] false), the body does not scroll on its own.
        Column((if (scrollable) Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()) else Modifier).padding(horizontal = SHEET_PAD),
            verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (showHint) Text(WhenSheet.hint(spec, anchorText), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            groups.forEach { g ->
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    SheetLabel(g.label)
                    TileGrid(g.tiles, spec.now) { t -> onPick(t.at, t.dateOnly, t) }
                }
            }
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                val customLabel = if (spec.verb == WhenSheet.Verb.REMIND) stringResource(R.string.cal_when_custom_before) else stringResource(R.string.cal_when_custom)
                val fromNow = stringResource(R.string.cal_when_from_now)
                val baseToggle: @Composable (Modifier) -> Unit = { m ->
                    SingleChoiceSegmentedButtonRow(m) {
                        listOf(WhenSheet.Base.NOW to fromNow, WhenSheet.Base.DUE to WhenSheet.baseLabel(spec.task)).forEachIndexed { i, (b, l) ->
                            SegmentedButton(selected = base == b, onClick = { base = b; WhenSheetPrefs.remember(context, spec.verb, b) },
                                shape = SegmentedButtonDefaults.itemShape(i, 2), icon = {}) { Text(l, maxLines = 1, style = MaterialTheme.typography.labelLarge) }
                        }
                    }
                }
                // The label and the toggle share a row when the sheet is wide enough for both (320 dp and
                // up: a phone). The toggle shows no check mark, so both labels fit; the filled
                // segment is the chosen one, and TalkBack reads it as selected.
                if (toggle) BoxWithConstraints(Modifier.fillMaxWidth()) {
                    if (maxWidth >= 320.dp) Row(verticalAlignment = Alignment.CenterVertically) {
                        SheetLabel(customLabel, Modifier.padding(end = 12.dp))
                        baseToggle(Modifier.weight(1f))
                    } else Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        SheetLabel(customLabel)
                        baseToggle(Modifier.fillMaxWidth())
                    }
                } else SheetLabel(customLabel)
                CompactDurationWheels(amount, unit, spec.verb == WhenSheet.Verb.REMIND) { a, u -> amount = a; unit = u }
                val line = if (spec.verb == WhenSheet.Verb.REMIND) WhenSheet.remindLine(result, spec.now)
                    else WhenSheet.resultLine(result, spec.now, resultDateOnly)
                val resultCd = stringResource(R.string.cal_when_result_cd)
                Text(if (blocked) stringResource(R.string.cal_when_already_passed, line) else line,
                    style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium,
                    color = if (past) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
                    modifier = Modifier.semantics { contentDescription = resultCd })
            }
        }
        Row(Modifier.fillMaxWidth().padding(start = 8.dp, end = SHEET_PAD, top = 8.dp, bottom = 4.dp),
            verticalAlignment = Alignment.CenterVertically) {
            if (onFull != null) TextButton(onClick = onFull) {
                Icon(Icons.Outlined.CalendarMonth, null, Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text(stringResource(R.string.cal_pick_date_time))
            }
            Spacer(Modifier.weight(1f))
            Button(onClick = { onPick(result, resultDateOnly, null) }, enabled = !blocked,
                contentPadding = PaddingValues(horizontal = 24.dp, vertical = 10.dp), modifier = Modifier.heightIn(min = 48.dp).testTag("when-confirm")) { Text(confirm) }
        }
    }
}

/** The status at the end of the when line (the quick-action sheet's header): Overdue 4 h, Missed, In progress, Snoozed. */
@Composable
internal fun StatusBadge(text: String, kind: String) {
    val (bg, fg) = when (kind) {
        "overdue", "missed" -> MaterialTheme.colorScheme.errorContainer to MaterialTheme.colorScheme.onErrorContainer
        "live" -> MaterialTheme.colorScheme.tertiaryContainer to MaterialTheme.colorScheme.onTertiaryContainer
        else -> MaterialTheme.colorScheme.secondaryContainer to MaterialTheme.colorScheme.onSecondaryContainer
    }
    Box(Modifier.background(bg, CircleShape).padding(horizontal = 8.dp, vertical = 2.dp)) {
        Text(text, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.SemiBold, color = fg, maxLines = 1)
    }
}

