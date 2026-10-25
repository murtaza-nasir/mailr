package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.SearchOff
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.shownDetail
import io.github.murtazanasir.mailr.ui.components.EmptyState
import kotlinx.coroutines.delay
import java.time.ZoneId

/** [text] with every word of [q] in bold (matched terms highlighted). */
fun highlight(text: String, q: String, color: Color): AnnotatedString = buildAnnotatedString {
    append(text)
    q.split(' ').map { it.trim() }.filter { it.length > 1 && ':' !in it }.forEach { w ->
        var i = text.indexOf(w, ignoreCase = true)
        while (i >= 0) {
            addStyle(SpanStyle(fontWeight = FontWeight.Bold, color = color), i, i + w.length)
            i = text.indexOf(w, i + w.length, ignoreCase = true)
        }
    }
}

/**
 * ACT event.search @android.calendar on Android: a full-screen search with recent queries. Results show as
 * Schedule rows, upcoming first, then past (most recent first); the search operators
 * go to the server as typed.
 */
@Composable
fun CalendarSearch(vm: CalendarViewModel, collections: List<CalendarCollection>, zone: ZoneId, onOpen: (CalendarEvent) -> Unit,
                   onClose: () -> Unit) {
    var q by remember { mutableStateOf("") }
    var results by remember { mutableStateOf<Pair<List<CalendarEvent>, List<CalendarEvent>>?>(null) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val recent by vm.prefs.recent.collectAsState()
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { catching { focus.requestFocus() } }
    val searchFailed = stringResource(R.string.cal_search_failed_dot)
    LaunchedEffect(q) {
        error = null
        if (q.isBlank()) { results = null; return@LaunchedEffect }
        delay(300)
        loading = true
        try { results = vm.search(q.trim()) } catch (x: ApiException) { error = x.shownDetail() } catch (x: Exception) { error = searchFailed }
        loading = false
    }
    fun open(e: CalendarEvent) { vm.prefs.addRecent(q); onOpen(e) }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
            Column(Modifier.fillMaxSize().imePadding()) {
                Row(Modifier.fillMaxWidth().padding(4.dp), verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = onClose) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.cal_back)) }
                    TextField(q, { q = it }, placeholder = { Text(stringResource(R.string.cal_search_placeholder)) }, singleLine = true,
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                        keyboardActions = KeyboardActions(onSearch = { vm.prefs.addRecent(q) }),
                        colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent),
                        modifier = Modifier.weight(1f).focusRequester(focus))
                    if (q.isNotEmpty()) IconButton(onClick = { q = "" }) { Icon(Icons.Outlined.Close, stringResource(R.string.cal_clear)) }
                }
                val r = results
                when {
                    q.isBlank() -> LazyColumn {
                        if (recent.isNotEmpty()) item("h") {
                            Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                                Text(stringResource(R.string.cal_search_recent), style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f).semantics { heading() })
                                TextButton(onClick = { vm.prefs.clearRecent() }) { Text(stringResource(R.string.cal_clear)) }
                            }
                        }
                        items(recent, key = { "r-$it" }) { s ->
                            ListItem(headlineContent = { Text(s) }, leadingContent = { Icon(Icons.Outlined.History, null) },
                                modifier = Modifier.clickable { q = s })
                        }
                        item("tips") {
                            Text(stringResource(R.string.cal_search_tips), style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(20.dp))
                        }
                    }
                    error != null -> EmptyState(Icons.Outlined.SearchOff, stringResource(R.string.cal_search_failed), error!!)
                    r == null || loading && r.first.isEmpty() && r.second.isEmpty() ->
                        Column(Modifier.fillMaxWidth().padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally) { CircularProgressIndicator() }
                    r.first.isEmpty() && r.second.isEmpty() -> EmptyState(Icons.Outlined.SearchOff, stringResource(R.string.cal_search_no_match), stringResource(R.string.cal_search_no_match_body))
                    else -> {
                        val hl = MaterialTheme.colorScheme.primary
                        val upcoming = stringResource(R.string.cal_search_upcoming)
                        val past = stringResource(R.string.cal_search_past)
                        LazyColumn(Modifier.fillMaxSize()) {
                            listOf(upcoming to r.first, past to r.second).forEach { (label, list) ->
                                if (list.isNotEmpty()) item("h-$label") {
                                    Text(label, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary,
                                        modifier = Modifier.padding(start = 20.dp, top = 16.dp, bottom = 4.dp).semantics { heading() })
                                }
                                items(list, key = { "$label-${it.key}" }) { e ->
                                    val col = collections.firstOrNull { it.url == e.calendar }
                                    ListItem(
                                        headlineContent = { Text(highlight(e.summary.ifBlank { stringResource(R.string.cal_no_title) }, q, hl)) },
                                        supportingContent = {
                                            Text(highlight(listOfNotNull(whenLine(e, zone), e.location?.takeIf { it.isNotBlank() }).joinToString(" · "), q, hl))
                                        },
                                        leadingContent = { ColorDot(CalendarColors.of(e.calendar, e.color ?: col?.color), 14) },
                                        modifier = Modifier.heightIn(min = 56.dp).clickable(onClickLabel = stringResource(R.string.cal_open_event)) { open(e) })
                                }
                            }
                            item("end") { Spacer(Modifier.width(1.dp).heightIn(min = 48.dp)) }
                        }
                    }
                }
            }
        }
    }
}
