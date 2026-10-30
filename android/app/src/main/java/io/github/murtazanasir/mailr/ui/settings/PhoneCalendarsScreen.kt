package io.github.murtazanasir.mailr.ui.settings

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.phonecal.PhoneCalMode
import io.github.murtazanasir.mailr.data.phonecal.PhoneCalendar
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import io.github.murtazanasir.mailr.look.ListItem
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import io.github.murtazanasir.mailr.ui.calendar.ColorDot
import io.github.murtazanasir.mailr.ui.findActivity
import io.github.murtazanasir.mailr.util.catching
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** What Settings → Calendar → Phone calendars shows. */
data class PhoneCalendarsUi(
    val enabled: Boolean,
    val granted: Boolean,
    /** Denied with "Don't ask again": only the app's system settings can allow access. */
    val deniedForever: Boolean,
    /** Denied just now (the switch went back off). */
    val deniedNow: Boolean = false,
    /** The phone's calendars; null while they load. */
    val calendars: List<PhoneCalendar>?,
    val modes: Map<Long, PhoneCalMode>,
)

class PhoneCalendarsCallbacks(
    val back: () -> Unit,
    val setEnabled: (Boolean) -> Unit,
    val setMode: (Long, PhoneCalMode) -> Unit,
    val openSystemSettings: () -> Unit,
)

@Composable
fun phoneModeLabel(m: PhoneCalMode): String = stringResource(when (m) {
    PhoneCalMode.OFF -> R.string.phonecal_mode_off
    PhoneCalMode.SHOW -> R.string.phonecal_mode_show
    PhoneCalMode.SEND -> R.string.phonecal_mode_send
    PhoneCalMode.DETAILS -> R.string.phonecal_mode_details
})

@Composable
fun PhoneCalendarsScreen(ui: PhoneCalendarsUi, cb: PhoneCalendarsCallbacks) {
    Scaffold(
        contentWindowInsets = WindowInsets(0),
        topBar = { TopAppBar(title = { Text(stringResource(R.string.phonecal_title)) },
            navigationIcon = { IconButton(onClick = cb.back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.settings_back)) } }) },
    ) { pad ->
        androidx.compose.runtime.CompositionLocalProvider(io.github.murtazanasir.mailr.look.LocalRowDividers provides true) {
            Box(Modifier.padding(pad).fillMaxSize(), contentAlignment = Alignment.TopCenter) {
                LazyColumn(Modifier.widthIn(max = 720.dp).fillMaxWidth().testTag("phone-calendars"), contentPadding = PaddingValues(bottom = 32.dp)) {
                    item(key = "switch") {
                        Toggle(stringResource(R.string.phonecal_switch), stringResource(R.string.phonecal_switch_sub), ui.enabled && ui.granted, cb.setEnabled)
                    }
                    if (!ui.granted && (ui.deniedForever || ui.deniedNow)) item(key = "denied") {
                        ListItem(headlineContent = {
                                Text(stringResource(if (ui.deniedForever) R.string.phonecal_denied else R.string.phonecal_denied_once))
                            },
                            leadingContent = { Icon(Icons.Outlined.ErrorOutline, null, tint = MaterialTheme.colorScheme.error) },
                            supportingContent = if (ui.deniedForever) ({
                                TextButton(onClick = cb.openSystemSettings, modifier = Modifier.heightIn(min = 48.dp).testTag("phonecal-open-settings")) {
                                    Text(stringResource(R.string.phonecal_open_settings))
                                }
                            }) else null,
                            modifier = Modifier.testTag("phonecal-denied"))
                    }
                    if (ui.enabled && ui.granted) {
                        item(key = "header") { Header(stringResource(R.string.phonecal_header)) }
                        val cals = ui.calendars
                        if (cals != null && cals.isEmpty()) item(key = "none") {
                            Text(stringResource(R.string.phonecal_none), color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(horizontal = 20.dp, vertical = 12.dp))
                        }
                        items(cals.orEmpty(), key = { "cal-${it.id}" }) { c -> CalendarRow(c, ui.modes[c.id] ?: PhoneCalMode.OFF) { cb.setMode(c.id, it) } }
                    }
                    item(key = "privacy") {
                        Text(stringResource(R.string.phonecal_privacy), style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 20.dp, vertical = 16.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun CalendarRow(c: PhoneCalendar, mode: PhoneCalMode, onPick: (PhoneCalMode) -> Unit) {
    var open by remember { mutableStateOf(false) }
    val label = phoneModeLabel(mode)
    val cd = stringResource(R.string.phonecal_choose_cd, c.name)
    Column {
        ListItem(headlineContent = { Text(c.name) },
            supportingContent = { Text(listOf(c.accountName, label).filter { it.isNotBlank() }.distinct().joinToString("\n")) },
            leadingContent = { ColorDot(Color(c.color or (0xFF shl 24)), size = 16) },
            modifier = Modifier.testTag("phonecal-${c.id}").clickable(onClickLabel = cd) { open = true }.semantics { contentDescription = "${c.name}, $label" })
        DropdownMenu(open, { open = false }) {
            PhoneCalMode.entries.forEach { m ->
                DropdownMenuItem({
                        if (m != PhoneCalMode.DETAILS) Text(phoneModeLabel(m))
                        else Column {
                            Text(phoneModeLabel(m))
                            // What leaves the phone with this choice, under it in the list.
                            Text(stringResource(R.string.phonecal_mode_details_hint), style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }, { open = false; if (m != mode) onPick(m) },
                    trailingIcon = if (m == mode) ({ Icon(Icons.Filled.Check, null) }) else null,
                    modifier = Modifier.testTag("phonecal-${c.id}-${m.name.lowercase()}"))
            }
        }
    }
}

/**
 * Settings → Calendar → Phone calendars. READ_CALENDAR is asked when the switch is turned on; a
 * denial leaves the switch off, and after "Don't ask again" the screen offers the app's system settings.
 */
@Composable
fun PhoneCalendarsRoute(g: AppGraph, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val pc = g.phoneCalendars
    val enabled by pc.prefs.enabled.collectAsStateWithLifecycle()
    val modes by pc.prefs.modes.collectAsStateWithLifecycle()
    val deniedForever by pc.prefs.deniedForever.collectAsStateWithLifecycle()
    var granted by remember { mutableStateOf(pc.source.granted()) }
    var deniedNow by remember { mutableStateOf(false) }
    var calendars by remember { mutableStateOf<List<PhoneCalendar>?>(null) }
    // Back from the system settings: the permission may have changed there.
    val owner = androidx.lifecycle.compose.LocalLifecycleOwner.current
    DisposableEffect(owner) {
        val obs = androidx.lifecycle.LifecycleEventObserver { _, e ->
            if (e == androidx.lifecycle.Lifecycle.Event.ON_RESUME) { pc.recheck(); granted = pc.source.granted() }
        }
        owner.lifecycle.addObserver(obs)
        onDispose { owner.lifecycle.removeObserver(obs) }
    }
    LaunchedEffect(enabled, granted) {
        calendars = if (enabled && granted) withContext(Dispatchers.IO) { pc.source.calendars() } else null
    }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        val activity = ctx.findActivity()
        val canAskAgain = activity != null && androidx.core.app.ActivityCompat.shouldShowRequestPermissionRationale(activity, Manifest.permission.READ_CALENDAR)
        granted = ok
        deniedNow = !ok
        pc.permissionAnswered(ok, canAskAgain)
    }
    PhoneCalendarsScreen(PhoneCalendarsUi(enabled, granted, deniedForever, deniedNow, calendars, modes), PhoneCalendarsCallbacks(
        back = onBack,
        setEnabled = { on ->
            deniedNow = false
            if (on && !pc.source.granted()) ask.launch(Manifest.permission.READ_CALENDAR) else pc.setEnabled(on)
        },
        setMode = pc::setMode,
        openSystemSettings = {
            catching { ctx.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", ctx.packageName, null))) }
        },
    ))
}
