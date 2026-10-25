package io.github.murtazanasir.mailr.ui.agenda

import android.Manifest
import android.appwidget.AppWidgetManager
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.ExperimentalMaterial3Api
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedTextField
import io.github.murtazanasir.mailr.look.RadioButton
import androidx.compose.material3.Scaffold
import io.github.murtazanasir.mailr.look.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import io.github.murtazanasir.mailr.look.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.weather.OpenMeteo
import io.github.murtazanasir.mailr.data.weather.WeatherLocation
import io.github.murtazanasir.mailr.data.weather.WeatherPlace
import io.github.murtazanasir.mailr.data.weather.WeatherSource
import io.github.murtazanasir.mailr.data.weather.WeatherStore
import io.github.murtazanasir.mailr.data.weather.WeatherUnits
import io.github.murtazanasir.mailr.data.weather.WeatherWork
import io.github.murtazanasir.mailr.ui.graph
import io.github.murtazanasir.mailr.ui.settings.Choice
import io.github.murtazanasir.mailr.ui.settings.Header
import io.github.murtazanasir.mailr.ui.settings.Toggle
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/** The Agenda widget's settings as the screen shows them. */
data class AgendaSettingsUi(
    val days: Int = AgendaModel.DEFAULT_DAYS,
    val weather: Boolean = true,
    val source: WeatherSource = WeatherSource.LOCATION,
    val locationGranted: Boolean = false,
    val city: WeatherPlace? = null,
    val units: WeatherUnits = WeatherUnits.AUTO,
    /** The chosen app's name, or null for Android's choice. */
    val appLabel: String? = null,
    val suggested: List<WeatherApps.App> = emptyList(),
    val all: List<WeatherApps.App> = emptyList(),
    /** This widget's style. */
    val style: AgendaStyle = AgendaStyle.DEFAULT,
    /** Location was refused (or is not wanted): the city search is open with a short hint. */
    val offerCity: Boolean = false,
    /** "Line up days and times" for this widget (AgendaColumns). */
    val columns: Boolean = true,
)

class AgendaSettingsCallbacks(
    val onDays: (Int) -> Unit,
    val onWeather: (Boolean) -> Unit,
    val onHere: () -> Unit,
    val onCity: (WeatherPlace) -> Unit,
    val onUnits: (WeatherUnits) -> Unit,
    val onApp: (WeatherApps.App?) -> Unit,
    val search: suspend (String) -> List<OpenMeteo.City>,
    val onDone: () -> Unit,
    val onStyle: (AgendaStyle) -> Unit = {},
    val onColumns: (Boolean) -> Unit = {},
)

/**
 * What the settings do about location for the weather (the source defaults to this phone's
 * location, so the permission is asked when the settings open with that source, not only when the
 * option is tapped). Pure, so the tests drive it with a fake permission.
 */
object WeatherPermissionFlow {
    enum class Step { NONE, ASK, OFFER_CITY }

    data class State(val weatherOn: Boolean, val source: WeatherSource, val granted: Boolean, val placeKnown: Boolean,
                     val denied: Boolean, val askedNow: Boolean = false)

    private fun wanted(s: State) = s.weatherOn && s.source == WeatherSource.LOCATION && !s.granted

    /** The settings open: ask, unless the person said no before (then the city search is offered). */
    fun onOpen(s: State): Step = when {
        !wanted(s) -> Step.NONE
        s.denied -> Step.OFFER_CITY
        s.askedNow -> Step.NONE
        else -> Step.ASK
    }

    /** The answer: a no is kept (no asking again on its own) and the city search opens. */
    fun onResult(granted: Boolean): Step = if (granted) Step.NONE else Step.OFFER_CITY

    /** Done with still no place: ask once if it was not asked in this visit; a no closes anyway ("Set weather" stays in the widget). */
    fun onDone(s: State): Step = when {
        !wanted(s) || s.placeKnown || s.askedNow || s.denied -> Step.NONE
        else -> Step.ASK
    }
}

/**
 * The Agenda widget's settings (android.widget): opened when the widget is placed, from its
 * long-press menu, and from "Set weather" in the widget. The style and "Line up days and times" (per widget), days to list, the
 * weather and where it is for, its units, and the app a tap on it opens. Kept on the phone
 * (AgendaStyles, WeatherStore); nothing here goes to the mailr server.
 */
class AgendaSettingsActivity : ComponentActivity() {
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(io.github.murtazanasir.mailr.i18n.AppLocales.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        // Placing the widget succeeds even when the screen is closed with Back.
        val id = intent.getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID)
        setResult(RESULT_OK, Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, id))
        val store = WeatherStore(this)
        val styles = AgendaStyles(this)
        val g = graph
        val ctx = this
        setContent {
            val prefs by g.settings.prefs.collectAsStateWithLifecycle(Prefs())
            var offerCity by rememberSaveable { mutableStateOf(false) }
            var asked by rememberSaveable { mutableStateOf(false) }
            var finishAfter by rememberSaveable { mutableStateOf(false) }
            var ui by remember { mutableStateOf(read(store, styles, id)) }
            val scope = rememberCoroutineScope()
            fun refreshUi() { ui = read(store, styles, id).copy(offerCity = offerCity) }
            fun changed() {
                refreshUi()
                WeatherWork.schedule(ctx)
                WeatherWork.soon(ctx)
                scope.launch { AgendaWidgets.refresh(ctx) }
            }
            fun locate() {
                // The last known location at once (the phone usually has one), so leaving this screen
                // early still leaves a place; then a fresh fix in the app's own scope, which outlives the
                // screen (Done before a slow fix arrived used to cancel it, and nothing
                // was kept, and the hourly fetch runs in the background, where Android gives no location).
                WeatherLocation.lastKnown(ctx)?.let { store.here = it; changed() }
                g.scope.launch {
                    try {
                        WeatherLocation.current(ctx)?.let { store.here = it }
                    } catch (e: CancellationException) {
                        throw e
                    } catch (_: Exception) {
                        // No fix now: the last known one (if any) stays.
                    }
                    WeatherWork.schedule(ctx)
                    WeatherWork.soon(ctx)
                    AgendaWidgets.refresh(ctx)
                }
                if (finishAfter) finish()
            }
            val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
                store.locationDenied = !ok
                offerCity = WeatherPermissionFlow.onResult(ok) == WeatherPermissionFlow.Step.OFFER_CITY
                refreshUi()
                if (ok) locate() else if (finishAfter) finish()
            }
            fun state() = WeatherPermissionFlow.State(store.enabled, store.source, WeatherLocation.granted(ctx), store.place != null,
                store.locationDenied, asked)
            fun launchAsk() { asked = true; ask.launch(Manifest.permission.ACCESS_COARSE_LOCATION) }
            androidx.compose.runtime.LaunchedEffect(Unit) {
                when (WeatherPermissionFlow.onOpen(state())) {
                    WeatherPermissionFlow.Step.ASK -> launchAsk()
                    WeatherPermissionFlow.Step.OFFER_CITY -> { offerCity = true; refreshUi() }
                    WeatherPermissionFlow.Step.NONE -> if (store.enabled && store.source == WeatherSource.LOCATION && store.here == null &&
                        WeatherLocation.granted(ctx)) locate()
                }
            }
            MailrTheme(prefs.theme, prefs.dynamicColor, prefs.webColours(), prefs.webLook) {
                AgendaSettingsScreen(ui, AgendaSettingsCallbacks(
                    onDays = { store.days = it; changed() },
                    onWeather = { store.enabled = it; changed() },
                    onHere = {
                        store.source = WeatherSource.LOCATION
                        changed()
                        // Tapped on purpose: asked even after an earlier no (Android decides whether to show the dialog).
                        if (!WeatherLocation.granted(ctx)) launchAsk() else locate()
                    },
                    onCity = { store.city = it; store.source = WeatherSource.CITY; offerCity = false; changed() },
                    onUnits = { store.units = it; changed() },
                    onApp = { store.app = it?.component; changed() },
                    search = { OpenMeteo().search(it) },
                    onDone = {
                        if (WeatherPermissionFlow.onDone(state()) == WeatherPermissionFlow.Step.ASK) { finishAfter = true; launchAsk() } else finish()
                    },
                    onStyle = { styles[id] = it; changed() },
                    onColumns = { styles.setColumns(id, it); changed() },
                ))
            }
        }
    }

    private fun read(store: WeatherStore, styles: AgendaStyles, id: Int): AgendaSettingsUi {
        val apps = WeatherApps.all(this)
        return AgendaSettingsUi(store.days, store.enabled, store.source, WeatherLocation.granted(this), store.city, store.units,
            store.app?.let { c -> apps.firstOrNull { it.component == c }?.label }, WeatherApps.suggested(this), apps, styles[id],
            columns = styles.columns(id))
    }
}
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AgendaSettingsScreen(ui: AgendaSettingsUi, cb: AgendaSettingsCallbacks) {
    val scope = rememberCoroutineScope()
    var query by remember { mutableStateOf("") }
    var results by remember { mutableStateOf<List<OpenMeteo.City>?>(null) }
    var failed by remember { mutableStateOf(false) }
    var pickApp by remember { mutableStateOf(false) }
    var allApps by remember { mutableStateOf(false) }
    fun search() {
        scope.launch {
            try {
                failed = false
                results = cb.search(query)
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                failed = true
            }
        }
    }
    Scaffold(topBar = {
        TopAppBar(title = { Text(stringResource(R.string.agenda_settings_title)) },
            actions = { TextButton(cb.onDone) { Text(stringResource(R.string.agenda_settings_done)) } })
    }) { pad ->
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(top = pad.calculateTopPadding(), bottom = pad.calculateBottomPadding() + 24.dp)) {
            item(key = "styleh") { Header(stringResource(R.string.agenda_settings_style)) }
            item(key = "style") { StylePicker(ui.style, cb.onStyle) }
            item(key = "columns") {
                Toggle(stringResource(R.string.agenda_settings_columns), stringResource(R.string.agenda_settings_columns_body), ui.columns, cb.onColumns)
            }
            item(key = "days") {
                Choice(stringResource(R.string.agenda_settings_days), ui.days.toString(), listOf(3, 5, 7, 10, 14).map { it.toString() to it },
                    stringResource(R.string.agenda_settings_days_body), cb.onDays)
            }
            item(key = "wh") { Header(stringResource(R.string.agenda_settings_weather)) }
            item(key = "w") { Toggle(stringResource(R.string.agenda_settings_weather), stringResource(R.string.agenda_settings_weather_body), ui.weather, cb.onWeather) }
            if (ui.weather) {
                item(key = "where") { Header(stringResource(R.string.agenda_settings_where)) }
                item(key = "here") {
                    val here = ui.source == WeatherSource.LOCATION
                    ListItem(headlineContent = { Text(stringResource(R.string.agenda_settings_here)) },
                        supportingContent = { Text(stringResource(if (here && !ui.locationGranted) R.string.agenda_settings_here_denied else R.string.agenda_settings_here_body)) },
                        leadingContent = { RadioButton(here, null) },
                        modifier = Modifier.selectable(here, role = Role.RadioButton, onClick = cb.onHere))
                }
                item(key = "city") {
                    val city = ui.source == WeatherSource.CITY
                    ListItem(headlineContent = { Text(stringResource(R.string.agenda_settings_city)) },
                        supportingContent = ui.city?.name?.let { n -> { Text(stringResource(R.string.agenda_settings_city_picked, n)) } },
                        leadingContent = { RadioButton(city, null) },
                        modifier = Modifier.selectable(city, role = Role.RadioButton) { ui.city?.let(cb.onCity) ?: run { results = results ?: emptyList() } })
                }
                if (ui.source == WeatherSource.CITY || results != null || ui.offerCity) {
                    if (ui.offerCity && ui.source != WeatherSource.CITY) item(key = "offer") {
                        Text(stringResource(R.string.agenda_settings_city_hint), style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp))
                    }
                    item(key = "search") {
                        OutlinedTextField(query, { query = it }, singleLine = true,
                            label = { Text(stringResource(R.string.agenda_settings_city_search)) },
                            trailingIcon = { Icon(Icons.Default.Search, stringResource(R.string.agenda_settings_city_search), Modifier.clickable { search() }) },
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search), keyboardActions = KeyboardActions(onSearch = { search() }),
                            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp))
                    }
                    if (failed) item(key = "failed") {
                        Text(stringResource(R.string.agenda_settings_search_failed), color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp))
                    }
                    results?.let { r ->
                        if (r.isEmpty() && query.isNotBlank() && !failed) item(key = "none") {
                            Text(stringResource(R.string.agenda_settings_city_none), style = MaterialTheme.typography.bodySmall,
                                modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp))
                        }
                        items(r, key = { "c${it.lat},${it.lon},${it.name}" }) { c ->
                            ListItem(headlineContent = { Text(c.name) }, supportingContent = c.region.takeIf { it.isNotBlank() }?.let { { Text(it) } },
                                modifier = Modifier.clickable {
                                    cb.onCity(WeatherPlace.rounded(c.lat, c.lon, c.name))
                                    results = null
                                    query = ""
                                })
                        }
                    }
                }
                item(key = "units") {
                    Header(stringResource(R.string.agenda_settings_units))
                    val opts = listOf(WeatherUnits.AUTO to R.string.agenda_settings_units_auto, WeatherUnits.CELSIUS to R.string.agenda_settings_units_c,
                        WeatherUnits.FAHRENHEIT to R.string.agenda_settings_units_f)
                    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp)) {
                        opts.forEachIndexed { i, (u, label) ->
                            SegmentedButton(ui.units == u, { cb.onUnits(u) }, SegmentedButtonDefaults.itemShape(i, opts.size)) { Text(stringResource(label)) }
                        }
                    }
                }
                item(key = "apph") { Header(stringResource(R.string.agenda_settings_app)) }
                item(key = "app") {
                    ListItem(headlineContent = { Text(ui.appLabel ?: stringResource(R.string.agenda_settings_app_default)) },
                        supportingContent = { Text(stringResource(R.string.agenda_settings_app_body)) },
                        modifier = Modifier.clickable { pickApp = !pickApp })
                }
                if (pickApp) {
                    item(key = "app-default") {
                        AppChoice(stringResource(R.string.agenda_settings_app_default), ui.appLabel == null) { cb.onApp(null); pickApp = false }
                    }
                    if (ui.suggested.isNotEmpty()) item(key = "app-sh") { SubHeader(stringResource(R.string.agenda_settings_app_weather)) }
                    items(ui.suggested, key = { "s" + it.component }) { a ->
                        AppChoice(a.label, ui.appLabel == a.label) { cb.onApp(a); pickApp = false }
                    }
                    if (!allApps) item(key = "app-any") {
                        ListItem(headlineContent = { Text(stringResource(R.string.agenda_settings_app_any), color = MaterialTheme.colorScheme.primary) },
                            modifier = Modifier.clickable { allApps = true })
                    } else {
                        item(key = "app-ah") { SubHeader(stringResource(R.string.agenda_settings_app_all)) }
                        items(ui.all, key = { "a" + it.component }) { a -> AppChoice(a.label, ui.appLabel == a.label) { cb.onApp(a); pickApp = false } }
                    }
                }
                item(key = "privacy") {
                    Text(stringResource(R.string.agenda_settings_privacy), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 20.dp, vertical = 16.dp))
                }
            }
        }
    }
}

/** The four styles in two rows, each with a picture of it (light or dark with the phone's theme). */
@Composable
private fun StylePicker(selected: AgendaStyle, onPick: (AgendaStyle) -> Unit) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp)) {
        AgendaStyle.entries.chunked(2).forEach { pair ->
            Row(Modifier.fillMaxWidth()) {
                pair.forEach { st ->
                    val on = st == selected
                    Column(Modifier.weight(1f).padding(4.dp).clip(RoundedCornerShape(16.dp))
                        .background(if (on) MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.surfaceContainerLow)
                        .selectable(on, role = Role.RadioButton) { onPick(st) }.padding(8.dp)) {
                        Image(painterResource(st.preview), null, Modifier.fillMaxWidth().aspectRatio(373f / 315f).clip(RoundedCornerShape(10.dp)),
                            contentScale = ContentScale.Crop)
                        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 4.dp)) {
                            RadioButton(on, null)
                            Text(stringResource(st.label), style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SubHeader(t: String) {
    Text(t, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(start = 20.dp, top = 8.dp, bottom = 2.dp))
}

@Composable
private fun AppChoice(label: String, selected: Boolean, onPick: () -> Unit) {
    ListItem(headlineContent = { Text(label) }, leadingContent = { RadioButton(selected, null) },
        modifier = Modifier.selectable(selected, role = Role.RadioButton, onClick = onPick))
}
