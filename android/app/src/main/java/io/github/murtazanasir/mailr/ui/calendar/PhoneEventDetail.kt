package io.github.murtazanasir.mailr.ui.calendar

import android.content.ActivityNotFoundException
import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.OpenInNew
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Place
import androidx.compose.material.icons.outlined.Smartphone
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.phonecal.PhoneEvents
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.time.AppClock
import java.time.ZoneId

/**
 * The small phone mark beside a phone calendar's event ("On this phone"). [described] false where
 * the row or block already says "On this phone" to TalkBack.
 */
@Composable
fun OnPhoneMark(tint: Color, size: Int = 14, described: Boolean = true) {
    Icon(Icons.Outlined.Smartphone, if (described) stringResource(R.string.phonecal_on_phone) else null, Modifier.size(size.dp), tint = tint)
}

/**
 * The detail of an event from a phone calendar: read-only, with "Open in Calendar" (the phone's
 * calendar app on that event). No edit, delete, answer, reminder or colour actions.
 */
@Composable
fun PhoneEventDetail(e: CalendarEvent, calendarName: String?, zone: ZoneId, onClose: (() -> Unit)?,
                     now: Long = AppClock.millis(), onOpenInCalendar: ((Intent) -> Unit)? = null) {
    val ctx = LocalContext.current
    val color = CalendarColors.of(e.calendar, e.color)
    val noApp = stringResource(R.string.phonecal_no_calendar_app)
    Column(Modifier.fillMaxWidth().testTag("phone-event-detail")) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            if (onClose != null) IconButton(onClick = onClose) { Icon(Icons.Outlined.Close, stringResource(R.string.cal_close)) }
        }
        Column(Modifier.verticalScroll(rememberScrollState()).padding(bottom = 24.dp)) {
            Row(Modifier.padding(start = 20.dp, end = 20.dp, top = 4.dp), verticalAlignment = Alignment.Top) {
                androidx.compose.foundation.layout.Box(Modifier.padding(top = 8.dp).size(16.dp).background(color, RoundedCornerShape(4.dp)))
                Spacer(Modifier.width(20.dp))
                Column {
                    Text(e.summary.ifBlank { stringResource(R.string.cal_no_title) }, style = MaterialTheme.typography.headlineSmall,
                        textDecoration = if (e.cancelled) TextDecoration.LineThrough else null, modifier = Modifier.semantics { heading() })
                    Spacer(Modifier.height(4.dp))
                    Text(whenLineZones(e, zone), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    statusChip(e, now)?.let { Text(it, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 6.dp)) }
                }
            }
            Spacer(Modifier.height(12.dp))
            e.location?.takeIf { it.isNotBlank() }?.let { where -> Line(Icons.Outlined.Place) { Text(where) } }
            Line(Icons.Outlined.CalendarMonth) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    ColorDot(color)
                    Spacer(Modifier.width(8.dp))
                    Text(calendarName ?: stringResource(R.string.cal_calendar_word), color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Line(Icons.Outlined.Smartphone) {
                Column {
                    Text(stringResource(R.string.phonecal_on_phone))
                    Text(stringResource(R.string.phonecal_detail_note), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            PhoneEvents.openIntent(e)?.let { intent ->
                Button(onClick = {
                    val i = Intent(intent).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    if (onOpenInCalendar != null) onOpenInCalendar(i)
                    else try { ctx.startActivity(i) } catch (_: ActivityNotFoundException) {
                        android.widget.Toast.makeText(ctx, noApp, android.widget.Toast.LENGTH_SHORT).show()
                    }
                }, modifier = Modifier.padding(horizontal = 20.dp, vertical = 12.dp).heightIn(min = 48.dp).testTag("open-in-calendar")) {
                    Icon(Icons.AutoMirrored.Outlined.OpenInNew, null, Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text(stringResource(R.string.phonecal_open_in_calendar))
                }
            }
        }
    }
}

@Composable
private fun Line(icon: androidx.compose.ui.graphics.vector.ImageVector, content: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 10.dp), verticalAlignment = Alignment.Top) {
        Icon(icon, null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(22.dp))
        Spacer(Modifier.width(16.dp))
        androidx.compose.foundation.layout.Box(Modifier.weight(1f)) { content() }
    }
}
