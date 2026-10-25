package io.github.murtazanasir.mailr.ui.components

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Label
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.Today
import androidx.compose.material.icons.outlined.Weekend
import androidx.compose.material.icons.outlined.WbSunny
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.Checkbox
import androidx.compose.material3.Icon
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import io.github.murtazanasir.mailr.look.OutlinedButton
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.ui.theme.Mail
import io.github.murtazanasir.mailr.ui.util.Format
import java.time.DayOfWeek
import java.time.ZonedDateTime
import java.time.temporal.TemporalAdjusters
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.platform.LocalDensity
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats

@Composable
fun Avatar(name: String?, email: String, size: Dp = 40.dp, modifier: Modifier = Modifier, selected: Boolean = false) {
    val bg = if (selected) MaterialTheme.colorScheme.primary else Format.avatarColor(email)
    val selectedDesc = stringResource(R.string.ui_selected)
    Box(
        modifier.size(size).clip(CircleShape).background(bg)
            .semantics { contentDescription = if (selected) selectedDesc else (name ?: email) },
        contentAlignment = Alignment.Center,
    ) {
        if (selected) {
            Icon(Icons.Filled.Check, null, tint = MaterialTheme.colorScheme.onPrimary, modifier = Modifier.size(size * 0.55f))
        } else {
            // Sized in dp, not sp: the circle does not grow with the text size, so the initials must not either.
            Text(Format.initials(name, email), color = Color.White, fontWeight = FontWeight.Medium,
                fontSize = with(LocalDensity.current) { (size * 0.4f).toSp() }, maxLines = 1, softWrap = false)
        }
    }
}

/** True when the app draws a dark theme (label colours pick their dark pair). */
@Composable
fun darkSurface(): Boolean = MaterialTheme.colorScheme.surface.luminance() < 0.5f

@Composable
fun LabelChip(label: Label, modifier: Modifier = Modifier, onRemove: (() -> Unit)? = null) {
    val (bg, fg) = Format.labelColors(label.color, darkSurface())
        ?: (MaterialTheme.colorScheme.secondaryContainer to MaterialTheme.colorScheme.onSecondaryContainer)
    Surface(color = bg, contentColor = fg, shape = RoundedCornerShape(6.dp), modifier = modifier) {
        Row(Modifier.padding(horizontal = 6.dp, vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(label.leaf, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (onRemove != null) {
                Text(" ×", style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.clickable(onClickLabel = stringResource(R.string.ui_remove_label, label.leaf)) { onRemove() })
            }
        }
    }
}

/** The importance marker: a small coral tag with a notched tail, as on the web (bits.tsx). */
@Composable
fun ImportanceMarker(important: Boolean, modifier: Modifier = Modifier, size: Dp = 14.dp) {
    if (!important) return
    val fill = Mail.colors.important
    val border = Mail.colors.importantBorder
    val desc = stringResource(R.string.ui_important)
    androidx.compose.foundation.Canvas(modifier.size(size).semantics { contentDescription = desc }) {
        // The web marker's path in its 18 x 12 box, scaled to the width and centred.
        val k = this.size.width / 18f
        val top = (this.size.height - 12f * k) / 2f
        fun x(v: Float) = v * k
        fun y(v: Float) = top + v * k
        val p = Path().apply {
            moveTo(x(1f), y(1f)); lineTo(x(11.5f), y(1f)); lineTo(x(16.5f), y(6f))
            lineTo(x(11.5f), y(11f)); lineTo(x(1f), y(11f)); lineTo(x(5.5f), y(6f)); close()
        }
        drawPath(p, fill)
        drawPath(p, border, style = androidx.compose.ui.graphics.drawscope.Stroke(
            width = 1.3f * k, join = androidx.compose.ui.graphics.StrokeJoin.Round))
    }
}

@Composable
fun Shimmer(modifier: Modifier) {
    val t = rememberInfiniteTransition(label = "shimmer") // i18n-ignore: animation label
    val a by t.animateFloat(0.35f, 0.8f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "a")
    Box(modifier.background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = a), RoundedCornerShape(6.dp)))
}

@Composable
fun ConversationSkeleton(rows: Int = 8) {
    val loading = stringResource(R.string.ui_loading)
    Column(Modifier.fillMaxWidth().semantics { contentDescription = loading }) {
        repeat(rows) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp)) {
                Shimmer(Modifier.size(40.dp).clip(CircleShape))
                Spacer(Modifier.width(16.dp))
                Column(Modifier.weight(1f)) {
                    Shimmer(Modifier.fillMaxWidth(0.45f).height(14.dp))
                    Spacer(Modifier.height(8.dp))
                    Shimmer(Modifier.fillMaxWidth(0.85f).height(12.dp))
                    Spacer(Modifier.height(6.dp))
                    Shimmer(Modifier.fillMaxWidth(0.7f).height(12.dp))
                }
            }
        }
    }
}

@Composable
fun EmptyState(icon: ImageVector, title: String, body: String? = null, modifier: Modifier = Modifier,
               action: Pair<String, () -> Unit>? = null) {
    Column(
        modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Surface(shape = CircleShape, color = MaterialTheme.colorScheme.secondaryContainer, modifier = Modifier.size(88.dp)) {
            Box(contentAlignment = Alignment.Center) {
                Icon(icon, null, Modifier.size(40.dp), tint = MaterialTheme.colorScheme.onSecondaryContainer)
            }
        }
        Spacer(Modifier.height(20.dp))
        Text(title, style = MaterialTheme.typography.titleMedium, textAlign = TextAlign.Center)
        if (body != null) {
            Spacer(Modifier.height(6.dp))
            Text(body, style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (action != null) {
            Spacer(Modifier.height(20.dp))
            OutlinedButton(onClick = action.second) { Text(action.first) }
        }
    }
}

// ---------------- snooze ----------------

data class SnoozeOption(val label: String, val icon: ImageVector, val at: ZonedDateTime)

fun snoozeOptions(now: ZonedDateTime = AppClock.now()): List<SnoozeOption> {
    val laterToday = now.plusHours(3).withMinute(0).withSecond(0).withNano(0)
    val tomorrow = now.plusDays(1).withHour(8).withMinute(0).withSecond(0).withNano(0)
    val weekend = now.with(TemporalAdjusters.next(DayOfWeek.SATURDAY)).withHour(8).withMinute(0).withSecond(0).withNano(0)
    val nextWeek = now.with(TemporalAdjusters.next(DayOfWeek.MONDAY)).withHour(8).withMinute(0).withSecond(0).withNano(0)
    return buildList {
        if (laterToday.toLocalDate() == now.toLocalDate() && laterToday.hour < 21) add(SnoozeOption(AppText.get(R.string.ui_snooze_later_today), Icons.Outlined.WbSunny, laterToday))
        add(SnoozeOption(AppText.get(R.string.ui_snooze_tomorrow), Icons.Outlined.Today, tomorrow))
        if (now.dayOfWeek !in setOf(DayOfWeek.FRIDAY, DayOfWeek.SATURDAY, DayOfWeek.SUNDAY))
            add(SnoozeOption(AppText.get(R.string.ui_snooze_weekend), Icons.Outlined.Weekend, weekend))
        add(SnoozeOption(AppText.get(R.string.ui_snooze_next_week), Icons.Outlined.CalendarMonth, nextWeek))
    }
}

@Composable
fun SnoozeSheet(onDismiss: () -> Unit, onPick: (ZonedDateTime) -> Unit, onCustom: () -> Unit) {
    val state = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = state) {
        Column(Modifier.navigationBarsPadding().padding(bottom = 12.dp)) {
            Text(stringResource(R.string.ui_snooze_until), style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(horizontal = 24.dp, vertical = 8.dp))
            val fmt = LocaleFormats.date("EEEhmma", "EEE, h:mm a")
            snoozeOptions().forEach { o ->
                ListItem(
                    headlineContent = { Text(o.label) },
                    trailingContent = { Text(o.at.format(fmt), color = MaterialTheme.colorScheme.onSurfaceVariant) },
                    leadingContent = { Icon(o.icon, null) },
                    colors = ListItemDefaults.colors(containerColor = Color.Transparent),
                    modifier = Modifier.clickable { onPick(o.at) },
                )
            }
            ListItem(
                headlineContent = { Text(stringResource(R.string.ui_pick_date_time)) },
                leadingContent = { Icon(Icons.Outlined.Schedule, null) },
                colors = ListItemDefaults.colors(containerColor = Color.Transparent),
                modifier = Modifier.clickable { onCustom() },
            )
        }
    }
}

// ---------------- labels ----------------

@Composable
fun LabelPickerSheet(
    labels: List<Label>,
    initiallyChecked: Set<Long>,
    title: String = stringResource(R.string.ui_label_as),
    onDismiss: () -> Unit,
    onApply: (add: List<Long>, remove: List<Long>) -> Unit,
) {
    val state = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var checked by remember { mutableStateOf(initiallyChecked) }
    var filter by remember { mutableStateOf("") }
    val user = labels.filter { it.role == null && it.name.contains(filter, ignoreCase = true) }.sortedBy { it.name.lowercase() }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = state) {
        Column(Modifier.navigationBarsPadding().padding(bottom = 8.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(horizontal = 24.dp))
            OutlinedTextField(filter, { filter = it }, placeholder = { Text(stringResource(R.string.ui_filter_labels)) }, singleLine = true,
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp))
            LazyColumn(Modifier.weight(1f, fill = false)) {
                items(user, key = { it.id }) { lb ->
                    val bg = Format.labelDot(lb.color, darkSurface()) ?: MaterialTheme.colorScheme.outline
                    ListItem(
                        headlineContent = { Text(lb.leaf) },
                        supportingContent = if (lb.depth > 0) ({ Text(lb.name.substringBeforeLast('/')) }) else null,
                        leadingContent = { Icon(Icons.AutoMirrored.Outlined.Label, null, tint = bg) },
                        trailingContent = {
                            Checkbox(lb.id in checked, onCheckedChange = {
                                checked = if (it) checked + lb.id else checked - lb.id
                            })
                        },
                        colors = ListItemDefaults.colors(containerColor = Color.Transparent),
                        modifier = Modifier.clickable {
                            checked = if (lb.id in checked) checked - lb.id else checked + lb.id
                        },
                    )
                }
            }
            Row(Modifier.fillMaxWidth().padding(16.dp), horizontalArrangement = Arrangement.End) {
                TextButton(onClick = onDismiss) { Text(stringResource(R.string.ui_cancel)) }
                Spacer(Modifier.width(8.dp))
                Button(onClick = {
                    onApply((checked - initiallyChecked).toList(), (initiallyChecked - checked).toList())
                }) { Text(stringResource(R.string.ui_apply)) }
            }
        }
    }
}

@Composable
fun BadgeCount(n: Int, modifier: Modifier = Modifier) {
    if (n <= 0) return
    Text(if (n > 999) "999+" else n.toString(), style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = modifier)
}

