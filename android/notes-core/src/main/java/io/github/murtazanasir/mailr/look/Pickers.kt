package io.github.murtazanasir.mailr.look

import io.github.murtazanasir.mailr.util.catching

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowRight
import androidx.compose.material.icons.outlined.Keyboard
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.DatePickerColors
import androidx.compose.material3.DatePickerDefaults
import androidx.compose.material3.DatePickerState
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TimePickerColors
import androidx.compose.material3.TimePickerDefaults
import androidx.compose.material3.TimePickerLayoutType
import androidx.compose.material3.TimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import io.github.murtazanasir.mailr.notes.R
import io.github.murtazanasir.mailr.time.AppClock
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.YearMonth
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.time.temporal.WeekFields
import java.util.Locale
import androidx.compose.material3.DatePicker as M3DatePicker
import androidx.compose.material3.DatePickerDialog as M3DatePickerDialog
import androidx.compose.material3.TimePicker as M3TimePicker

/*
 * Date and time pickers. In the web's look they are
 * the web's when-picker (tasks.css .tk-when): a typed field first, then a compact month grid for a date,
 * or the typed time with Android's clock one tap away. They read and write Material's own picker
 * states, so the screens and their tests keep working; in Material's look each is Material's.
 */

/**
 * The dialog around a [DatePicker]. In the web's look a dialog of the web's (24 dp corners, the dialog
 * colour) with the buttons at the end, as the web's dialogs.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DatePickerDialog(
    onDismissRequest: () -> Unit,
    confirmButton: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    dismissButton: @Composable (() -> Unit)? = null,
    shape: Shape = DatePickerDefaults.shape,
    tonalElevation: Dp = DatePickerDefaults.TonalElevation,
    colors: DatePickerColors = DatePickerDefaults.colors(),
    properties: DialogProperties = DialogProperties(usePlatformDefaultWidth = false),
    content: @Composable ColumnScope.() -> Unit,
) {
    if (!look.web) return M3DatePickerDialog(onDismissRequest, confirmButton, modifier, dismissButton, shape, tonalElevation, colors,
        properties, content)
    val bg = look.palette.let { if (look.dark) it.surfaceContainer else it.surface }
    Dialog(onDismissRequest, properties) {
        Surface(modifier.padding(horizontal = 24.dp).widthIn(max = 360.dp), shape = MaterialTheme.shapes.extraLarge, color = bg) {
            Column {
                content()
                Row(Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, bottom = 12.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End)) {
                    dismissButton?.invoke()
                    confirmButton()
                }
            }
        }
    }
}

/**
 * A date. In the web's look, the typed field (a date in the phone's own short or medium form, an ISO
 * date, or a day and month) with what it read below it, then the month grid (tasks.css .tk-mini-*):
 * the weekday letters from the phone's first day of the week, today in the primary colour, the chosen
 * day on a primary circle, the days of the months around in the tertiary text colour. Typing is
 * Android's keyboard input; the grid is its calendar. Writes [DatePickerState.selectedDateMillis]
 * (UTC midnight, as Material's) and respects its selectable dates and years.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DatePicker(
    state: DatePickerState,
    modifier: Modifier = Modifier,
    showModeToggle: Boolean = true,
    colors: DatePickerColors = DatePickerDefaults.colors(),
) {
    if (!look.web) return M3DatePicker(state = state, modifier = modifier, showModeToggle = showModeToggle, colors = colors)
    val locale = Locale.getDefault()
    val p = look.palette
    val cs = MaterialTheme.colorScheme
    val selected = state.selectedDateMillis?.let { Instant.ofEpochMilli(it).atZone(ZoneOffset.UTC).toLocalDate() }
    val today = remember { LocalDate.now(AppClock.clock()) } // clock-ok: AppClock's own clock
    val month = Instant.ofEpochMilli(state.displayedMonthMillis).atZone(ZoneOffset.UTC).toLocalDate().let { YearMonth.from(it) }
    val shortForm = remember(locale) { DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM).withLocale(locale) }
    val full = remember(locale) { DateTimeFormatter.ofLocalizedDate(FormatStyle.FULL).withLocale(locale) }
    var text by rememberSaveable { mutableStateOf(selected?.format(shortForm).orEmpty()) }
    val typed = WhenText.date(text, locale, month.year)
    fun allowed(d: LocalDate) = d.year in state.yearRange && state.selectableDates.isSelectableDate(utcMillis(d)) &&
        state.selectableDates.isSelectableYear(d.year)
    fun pick(d: LocalDate) {
        if (!allowed(d)) return
        state.selectedDateMillis = utcMillis(d)
        state.displayedMonthMillis = utcMillis(d.withDayOfMonth(1))
        text = d.format(shortForm)
    }
    // The field and the month line keep 16 dp sides; the grid keeps 4, so its days are as wide as the dialog allows.
    Column(modifier.fillMaxWidth().padding(top = 16.dp)) {
        Box(Modifier.padding(horizontal = 16.dp)) {
            WhenField(stringResource(R.string.look_date), text, invalid = text.isNotBlank() && (typed == null || !allowed(typed)),
                onChange = { v ->
                    text = v
                    WhenText.date(v, locale, month.year)?.takeIf(::allowed)?.let { d ->
                        state.selectedDateMillis = utcMillis(d)
                        state.displayedMonthMillis = utcMillis(d.withDayOfMonth(1))
                    }
                })
        }
        val bad = text.isNotBlank() && (typed == null || !allowed(typed))
        Text(if (bad) stringResource(R.string.look_not_a_date, today.format(shortForm)) else selected?.format(full).orEmpty(),
            style = TextStyle(fontSize = 12.5.sp, lineHeight = 18.sp), color = if (bad) cs.error else p.text2,
            modifier = Modifier.heightIn(min = 18.dp).padding(start = 18.dp, end = 16.dp, top = 6.dp, bottom = 4.dp))
        // .tk-mini-head: the month, and the months either side.
        Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(month.atDay(1).format(DateTimeFormatter.ofPattern("LLLL yyyy", locale)).replaceFirstChar { it.titlecase(locale) },
                style = TextStyle(fontSize = 13.sp, lineHeight = 18.sp, fontWeight = FontWeight.SemiBold), color = p.text,
                modifier = Modifier.weight(1f).padding(start = 2.dp))
            IconButton(onClick = { state.displayedMonthMillis = utcMillis(month.minusMonths(1).atDay(1)) },
                enabled = month.minusMonths(1).year in state.yearRange) {
                Icon(Icons.AutoMirrored.Outlined.KeyboardArrowLeft, stringResource(R.string.look_previous_month), tint = p.text2)
            }
            IconButton(onClick = { state.displayedMonthMillis = utcMillis(month.plusMonths(1).atDay(1)) },
                enabled = month.plusMonths(1).year in state.yearRange) {
                Icon(Icons.AutoMirrored.Outlined.KeyboardArrowRight, stringResource(R.string.look_next_month), tint = p.text2)
            }
        }
        val first = WeekFields.of(locale).firstDayOfWeek
        Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp)) {
            (0 until 7).forEach { i ->
                Text(first.plus(i.toLong()).getDisplayName(java.time.format.TextStyle.NARROW, locale), Modifier.weight(1f),
                    style = TextStyle(fontSize = 11.sp, lineHeight = 24.sp, textAlign = TextAlign.Center), color = p.text3)
            }
        }
        val start = month.atDay(1).let { d -> d.minusDays(((d.dayOfWeek.value - first.value + 7) % 7).toLong()) }
        (0 until 6).forEach { w ->
            Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp)) {
                (0 until 7).forEach { i ->
                    val d = start.plusDays((w * 7 + i).toLong())
                    val on = d == selected
                    val out = YearMonth.from(d) != month
                    val can = allowed(d)
                    Box(
                        // 48 dp tall, a seventh of the grid wide: 48 dp from a 336 dp grid (the 360 dp dialog on a
                        // phone 384 dp wide or more). On a narrower phone the touch target still reaches 48 dp past
                        // the cell (Compose's minimum touch size), and the month keeps its seven columns.
                        Modifier.weight(1f).height(48.dp)
                            .selectable(on, enabled = can, role = Role.Button, onClick = { pick(d) })
                            .semantics { contentDescription = d.format(full) }
                            .alpha(if (can) 1f else 0.38f),
                        contentAlignment = Alignment.Center,
                    ) {
                        Box(Modifier.size(32.dp).background(if (on) cs.primary else Color.Transparent, CircleShape),
                            contentAlignment = Alignment.Center) {
                            Text(d.dayOfMonth.toString(),
                                style = TextStyle(fontSize = 12.5.sp, fontWeight = if (d == today && !on) FontWeight.Bold else FontWeight.Normal),
                                color = when {
                                    on -> cs.onPrimary
                                    d == today -> cs.primary
                                    out -> p.text3
                                    else -> p.text
                                })
                        }
                    }
                }
            }
        }
    }
}

private fun utcMillis(d: LocalDate): Long = d.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()

/**
 * A time. In the web's look, the typed field (tasks.css .tk-time-input: "9:30", "930", "9:30 pm",
 * "21:30") with what it read below it, and a button that opens Android's clock (Material's dial, with its
 * own keyboard input) under the field. Writes the [TimePickerState]'s hour and minute.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TimePicker(
    state: TimePickerState,
    modifier: Modifier = Modifier,
    colors: TimePickerColors = TimePickerDefaults.colors(),
    layoutType: TimePickerLayoutType = TimePickerDefaults.layoutType(),
) {
    if (!look.web) return M3TimePicker(state, modifier, colors, layoutType)
    val locale = Locale.getDefault()
    val p = look.palette
    val cs = MaterialTheme.colorScheme
    val form = remember(locale, state.is24hour) { DateTimeFormatter.ofPattern(if (state.is24hour) "H:mm" else "h:mm a", locale) }
    var text by rememberSaveable { mutableStateOf(LocalTime.of(state.hour, state.minute).format(form)) }
    var clock by rememberSaveable { mutableStateOf(false) }
    // The clock moved the time: the field follows (typing that already says this time is left alone).
    LaunchedEffect(state.hour, state.minute) {
        val now = LocalTime.of(state.hour, state.minute)
        if (WhenText.time(text, locale) != now) text = now.format(form)
    }
    val typed = WhenText.time(text, locale)
    val bad = text.isNotBlank() && typed == null
    Column(modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        Row(verticalAlignment = Alignment.Bottom) {
            Box(Modifier.weight(1f)) {
                WhenField(stringResource(R.string.look_time), text, invalid = bad, onChange = { v ->
                    text = v
                    WhenText.time(v, locale)?.let { t -> state.hour = t.hour; state.minute = t.minute }
                })
            }
            IconButton(onClick = { clock = !clock }) {
                Icon(if (clock) Icons.Outlined.Keyboard else Icons.Outlined.Schedule,
                    stringResource(if (clock) R.string.look_type_time else R.string.look_use_clock), tint = p.text2)
            }
        }
        Text(if (bad) stringResource(R.string.look_not_a_time, LocalTime.of(9, 30).format(form)) else typed?.format(form)?.takeIf { it != text.trim() }.orEmpty(),
            style = TextStyle(fontSize = 12.5.sp, lineHeight = 18.sp), color = if (bad) cs.error else p.text2,
            modifier = Modifier.fillMaxWidth().heightIn(min = 18.dp).padding(start = 2.dp, top = 6.dp, bottom = 4.dp))
        if (clock) M3TimePicker(state, Modifier.padding(top = 8.dp), colors, layoutType)
    }
}

/** tasks.css .tk-when-input: the label above, a 40 dp box on the input fill, a focus ring, the danger ring when it reads nothing. */
@Composable
private fun WhenField(label: String, value: String, invalid: Boolean, onChange: (String) -> Unit) {
    val p = look.palette
    val cs = MaterialTheme.colorScheme
    val source = remember { MutableInteractionSource() }
    val focused by source.collectIsFocusedAsState()
    val shape = RoundedCornerShape(WebTokens.RadiusSm)
    BasicTextField(value, onChange, Modifier.fillMaxWidth(), singleLine = true, interactionSource = source,
        textStyle = WebTheme.style(WebTokens.Body).copy(color = p.text), cursorBrush = SolidColor(cs.primary),
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
        decorationBox = { inner ->
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(label, style = WebTheme.style(WebTokens.FieldLabel), color = p.text2)
                Box(
                    Modifier.fillMaxWidth().heightIn(min = 40.dp)
                        .background(if (focused) p.inputBgFocus else p.inputBg, shape)
                        .then(when {
                            invalid -> Modifier.border(2.dp, cs.error, shape)
                            focused -> Modifier.border(2.dp, p.focusRing, shape)
                            else -> Modifier
                        })
                        .padding(horizontal = 10.dp, vertical = 10.dp),
                    contentAlignment = Alignment.CenterStart,
                ) { inner() }
            }
        })
}

/** Reading typed dates and times (the web's when-picker accepts the same kinds of text). */
object WhenText {
    /**
     * A date from [text]: the [locale]'s short, medium, long or full form, an ISO date, or a day and month
     * in the locale's order or with a month name (then the year is [year]). Null when it reads none.
     */
    fun date(text: String, locale: Locale, year: Int): LocalDate? {
        val s = text.trim().replace(Regex("\\s+"), " ")
        if (s.isEmpty()) return null
        val full = listOf(FormatStyle.SHORT, FormatStyle.MEDIUM, FormatStyle.LONG, FormatStyle.FULL).map { style ->
            java.time.format.DateTimeFormatterBuilder().parseCaseInsensitive().parseLenient().appendLocalized(style, null).toFormatter(locale)
        } + DateTimeFormatter.ISO_LOCAL_DATE
        full.forEach { f -> catching { return LocalDate.parse(s, f) } }
        val withYear = listOf("d MMM yyyy", "MMM d yyyy", "MMM d, yyyy", "d MMMM yyyy", "MMMM d yyyy", "MMMM d, yyyy")
        withYear.forEach { pat -> catching { return LocalDate.parse(s, caseless(pat, locale)) } }
        val short = shortPatternWithoutYear(locale)
        val noYear = listOfNotNull(short, "MMM d", "d MMM", "MMMM d", "d MMMM")
        noYear.forEach { pat ->
            catching {
                val f = java.time.format.DateTimeFormatterBuilder().parseCaseInsensitive().parseLenient().appendPattern(pat)
                    .parseDefaulting(java.time.temporal.ChronoField.YEAR, year.toLong()).toFormatter(locale)
                return LocalDate.parse(s, f)
            }
        }
        return null
    }

    private fun caseless(pattern: String, locale: Locale): DateTimeFormatter =
        java.time.format.DateTimeFormatterBuilder().parseCaseInsensitive().parseLenient().appendPattern(pattern).toFormatter(locale)

    /** The locale's short date pattern with its year taken out ("M/d/yy" → "M/d", "dd.MM.yy" → "dd.MM"). */
    internal fun shortPatternWithoutYear(locale: Locale): String? {
        val pat = java.time.format.DateTimeFormatterBuilder.getLocalizedDateTimePattern(FormatStyle.SHORT, null,
            java.time.chrono.IsoChronology.INSTANCE, locale)
        val out = pat.replace(Regex("[^dMLy]*y+[^dMLy]*"), "").trim()
        return out.takeIf { it.contains('d') && (it.contains('M') || it.contains('L')) }?.replace('L', 'M')
    }

    private val clock = Regex("^(\\d{1,2})(?:[:.h]?(\\d{2}))?$")

    /**
     * A time from [text]: "9", "9:30", "930", "09.30", "21:30", each with an optional am/pm (the
     * [locale]'s own words too). Null when it reads none.
     */
    fun time(text: String, locale: Locale): LocalTime? {
        var s = text.trim().lowercase(locale).replace(" ", "")
        if (s.isEmpty()) return null
        val symbols = java.text.DateFormatSymbols.getInstance(locale).amPmStrings.map { it.lowercase(locale).replace(" ", "") }
        var half: Int? = null
        val marks = listOf(symbols[0] to 0, symbols[1] to 1, "a.m." to 0, "p.m." to 1, "am" to 0, "pm" to 1, "a" to 0, "p" to 1)
        for ((mark, h) in marks) {
            if (mark.isNotEmpty() && s.endsWith(mark)) { half = h; s = s.removeSuffix(mark); break }
            if (mark.isNotEmpty() && s.startsWith(mark)) { half = h; s = s.removePrefix(mark); break }
        }
        val m = clock.matchEntire(s) ?: return null
        var hour = m.groupValues[1].toInt()
        val minute = m.groupValues[2].ifEmpty { "0" }.toInt()
        if (minute > 59) return null
        if (half != null) {
            if (hour !in 1..12) return null
            hour = hour % 12 + 12 * half
        } else if (hour > 23) return null
        return LocalTime.of(hour, minute)
    }
}
