package io.github.murtazanasir.mailr.notes.ui

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.notes.R
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.stringResource
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.outlined.Block
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.notes.data.Label
import io.github.murtazanasir.mailr.notes.data.NOTE_COLORS
import java.time.Instant
import java.time.ZoneId
import java.util.Locale

/**
 * Note colours: the web's swatches (frontend/src/features/notes/notes.css), light and dark.
 * Colour is never the only signal: every swatch has a name, and the menus show it.
 */
object NoteColors {
    private val light = mapOf(
        "tomato" to 0xFFFAAFA8, "flamingo" to 0xFFF6E2DD, "tangerine" to 0xFFF9C6A8, "banana" to 0xFFFFF8B8,
        "sage" to 0xFFE2F6D3, "basil" to 0xFFB4DDD3, "peacock" to 0xFFD4E4ED, "blueberry" to 0xFFAECCDC,
        "lavender" to 0xFFDCD6F7, "grape" to 0xFFD3BFDB, "graphite" to 0xFFE8E8EB, "cocoa" to 0xFFE9E3D4,
    )
    private val dark = mapOf(
        "tomato" to 0xFF77172E, "flamingo" to 0xFF6C394F, "tangerine" to 0xFF692B17, "banana" to 0xFF7C4A03,
        "sage" to 0xFF264D3B, "basil" to 0xFF0C625D, "peacock" to 0xFF256377, "blueberry" to 0xFF284255,
        "lavender" to 0xFF3C3F6B, "grape" to 0xFF472E5B, "graphite" to 0xFF3C3F43, "cocoa" to 0xFF4B443A,
    )

    /** The swatch's name in the app's language ("Tomato"; "Default" for none). */
    fun name(c: String?): String = when (c) {
        null -> AppText.get(R.string.notes_color_default)
        "tomato" -> AppText.get(R.string.notes_color_tomato)
        "flamingo" -> AppText.get(R.string.notes_color_flamingo)
        "tangerine" -> AppText.get(R.string.notes_color_tangerine)
        "banana" -> AppText.get(R.string.notes_color_banana)
        "sage" -> AppText.get(R.string.notes_color_sage)
        "basil" -> AppText.get(R.string.notes_color_basil)
        "peacock" -> AppText.get(R.string.notes_color_peacock)
        "blueberry" -> AppText.get(R.string.notes_color_blueberry)
        "lavender" -> AppText.get(R.string.notes_color_lavender)
        "grape" -> AppText.get(R.string.notes_color_grape)
        "graphite" -> AppText.get(R.string.notes_color_graphite)
        "cocoa" -> AppText.get(R.string.notes_color_cocoa)
        else -> c.replaceFirstChar { it.titlecase(Locale.ROOT) }
    }

    /** A swatch as an ARGB value outside Compose (launcher icons, widgets), or null for none. */
    fun argb(c: String?, dark: Boolean = false): Int? = c?.let { (if (dark) this.dark else light)[it] }?.toInt()

    @Composable
    fun background(c: String?): Color {
        val dark = MaterialTheme.colorScheme.background.luminance() < 0.5f
        val v = c?.let { (if (dark) this.dark else light)[it] } ?: return MaterialTheme.colorScheme.surfaceContainerLow
        return Color(v)
    }

    /** Text on a coloured card: computed contrast (dark text on the light swatches, light on the dark ones). */
    @Composable
    fun content(c: String?): Color {
        if (c == null) return MaterialTheme.colorScheme.onSurface
        return if (background(c).luminance() > 0.45f) Color(0xFF1F1F1F) else Color(0xFFF1F1F1)
    }
}

/** A dashed edge: a change made on the phone the server has not confirmed yet. */
fun Modifier.pendingEdge(pending: Boolean, color: Color, radius: Float = 24f): Modifier = if (!pending) this else drawBehind {
    drawRoundRect(color, cornerRadius = CornerRadius(radius, radius),
        style = Stroke(width = 2.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(10f, 8f))))
}

/** "Edited 3:14 PM", "Edited 3 Oct". */
fun editedLabel(iso: String?, now: Long = AppClock.millis()): String? {
    val t = iso?.let { catching { Instant.parse(it) }.getOrNull() ?: catching { java.time.OffsetDateTime.parse(it).toInstant() }.getOrNull() }
        ?: return null
    val z = ZoneId.systemDefault()
    val d = t.atZone(z)
    val today = Instant.ofEpochMilli(now).atZone(z).toLocalDate()
    return AppText.get(R.string.notes_edited_at, if (d.toLocalDate() == today) d.format(LocaleFormats.date("hmma", "h:mm a"))
    else d.format(LocaleFormats.date("dMMM", "d MMM")))
}

/** "Deleted 3 days ago · 27 days left". */
fun trashLabel(deletedAt: String?, now: Long = AppClock.millis()): String {
    val t = deletedAt?.let { catching { java.time.OffsetDateTime.parse(it).toInstant() }.getOrNull() } ?: return AppText.get(R.string.notes_in_trash)
    val days = ((now - t.toEpochMilli()) / 86_400_000L).toInt().coerceAtLeast(0)
    val left = (30 - days).coerceAtLeast(0)
    val ago = when (days) {
        0 -> AppText.get(R.string.notes_deleted_today)
        1 -> AppText.get(R.string.notes_deleted_yesterday)
        else -> AppText.plural(R.plurals.notes_deleted_days_ago, days, days)
    }
    return AppText.get(R.string.notes_trash_label, ago, AppText.plural(R.plurals.notes_trash_days_left, left, left))
}

/** The colour menu: Default and the 12 swatches, each with its name (never colour alone). */
@Composable
fun ColorDialog(current: String?, onPick: (String?) -> Unit, onDismiss: () -> Unit) {
    AlertDialog(onDismissRequest = onDismiss, confirmButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.notes_close)) } },
        title = { Text(stringResource(R.string.notes_colour)) },
        text = {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                (listOf<String?>(null) + NOTE_COLORS).forEach { c ->
                    val name = NoteColors.name(c)
                    Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier
                        .width(64.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .clickable(role = Role.RadioButton) { onPick(c) }
                        .semantics(mergeDescendants = true) { contentDescription = name; selected = c == current }
                        .padding(4.dp)) {
                        Box(Modifier.size(40.dp).clip(CircleShape).background(NoteColors.background(c))
                            .border(BorderStroke(if (c == current) 3.dp else 1.dp,
                                if (c == current) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline), CircleShape),
                            contentAlignment = Alignment.Center) {
                            if (c == current) Icon(Icons.Filled.Check, null, tint = NoteColors.content(c))
                            else if (c == null) Icon(Icons.Outlined.Block, null, tint = MaterialTheme.colorScheme.outline)
                        }
                        Text(name, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        })
}

/**
 * The labels menu (ACT note.label @android.notes @android.notesapp, ACT table.label @android.notes @android.notesapp): this viewer's labels with checkboxes, a filter line
 * and "Create 'name'" (ACT notelabel.create @android.notes @android.notesapp).
 */
@Composable
fun LabelsDialog(all: List<Label>, on: List<String>, onSave: (List<String>, String?) -> Unit, onDismiss: () -> Unit) {
    var chosen by remember { mutableStateOf(on.toSet()) }
    var filter by remember { mutableStateOf("") }
    var creating by remember { mutableStateOf(false) }
    AlertDialog(onDismissRequest = onDismiss,
        confirmButton = { TextButton(onClick = { onSave(chosen.toList(), null) }) { Text(stringResource(R.string.notes_done)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.notes_cancel)) } },
        title = { Text(stringResource(R.string.notes_labels)) },
        text = {
            Column {
                // A filter line once the list is long; a short list shows every label.
                if (all.size > 8) OutlinedTextField(filter, { filter = it }, singleLine = true, label = { Text(stringResource(R.string.notes_find_label)) },
                    modifier = Modifier.fillMaxWidth())
                val shown = all.filter { it.name.contains(filter.trim(), ignoreCase = true) }
                LazyColumn(Modifier.heightIn(max = 320.dp)) {
                    items(shown, key = { it.name }) { l ->
                        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()
                            .heightIn(min = 48.dp)
                            .clickable(role = Role.Checkbox) { chosen = if (l.name in chosen) chosen - l.name else chosen + l.name }) {
                            Checkbox(l.name in chosen, null)
                            Spacer(Modifier.width(8.dp))
                            Text(l.name)
                        }
                    }
                }
                HorizontalDivider()
                Text(stringResource(R.string.notes_new_label), color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable { creating = true }.padding(vertical = 14.dp))
            }
        })
    if (creating) NameDialog(stringResource(R.string.notes_new_label), "", stringResource(R.string.notes_create), onDismiss = { creating = false }) { v ->
        creating = false
        val n = v.trim()
        if (n.isNotEmpty()) onSave((chosen + n).toList(), n.takeIf { all.none { l -> l.name.equals(it, ignoreCase = true) } })
    }
}

/** A one-line text dialog (a new label, a rename, a new section, the extra line). */
@Composable
fun NameDialog(title: String, initial: String, confirm: String, onDismiss: () -> Unit, onDone: (String) -> Unit) {
    var v by remember { mutableStateOf(initial) }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(title) },
        text = { OutlinedTextField(v, { v = it }, singleLine = true, modifier = Modifier.fillMaxWidth().testTag("dialog-field")) },
        confirmButton = { TextButton(onClick = { onDone(v) }) { Text(confirm) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.notes_cancel)) } })
}

/** A small rounded tag (labels on cards, quantity chips). */
@Composable
fun Tag(text: String, modifier: Modifier = Modifier, color: Color = MaterialTheme.colorScheme.surfaceVariant,
        content: Color = MaterialTheme.colorScheme.onSurfaceVariant) {
    Surface(shape = RoundedCornerShape(6.dp), color = color, modifier = modifier) {
        Text(text, style = MaterialTheme.typography.labelMedium, color = content, maxLines = 1,
            overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp))
    }
}
