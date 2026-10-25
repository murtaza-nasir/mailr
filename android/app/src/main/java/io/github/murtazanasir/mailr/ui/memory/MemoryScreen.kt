package io.github.murtazanasir.mailr.ui.memory

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.automirrored.outlined.OpenInNew
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Badge
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Clear
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.PushPin
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.TaskAlt
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import io.github.murtazanasir.mailr.look.ExtendedFloatingActionButton
import io.github.murtazanasir.mailr.look.FilledTonalButton
import io.github.murtazanasir.mailr.look.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.PlainTooltip
import androidx.compose.material3.Scaffold
import io.github.murtazanasir.mailr.look.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.material3.TooltipBox
import androidx.compose.material3.TooltipDefaults
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.material3.VerticalDivider
import androidx.compose.material3.rememberTooltipState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.LearningStatus
import io.github.murtazanasir.mailr.data.api.Memory
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.util.Format
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

/** What the Memory page can do. */
data class MemoryCallbacks(
    val back: () -> Unit,
    val retry: () -> Unit,
    val setQuery: (String) -> Unit,
    val setKind: (String?) -> Unit,
    val learnNow: () -> Unit,
    val keep: (List<Long>) -> Unit,
    val dismiss: (List<Long>) -> Unit,
    /** Add memory: the sheet, empty. */
    val add: () -> Unit,
    val edit: (Memory) -> Unit,
    val delete: (Memory) -> Unit,
    val pin: (Memory) -> Unit,
    val makeTask: (Memory) -> Unit,
    /** A person's card (People → Open card), by their entry's id. */
    val openCard: (Long) -> Unit,
    /** A memory's Source: the email it came from. */
    val openSource: ((Long) -> Unit)? = null,
    /** "From a chat": the chat a memory came from, at the message (session id, chat message id). */
    val openChat: ((String, Long?) -> Unit)? = null,
)

/**
 * The Memory page on Android: the web's Settings → Ambrose → Memory with
 * the same sections, order and words. How learning went and Learn now; one search and the kind chips
 * over every section; To review (Keep, Dismiss, Keep all); About you; People (a card per person);
 * Topics. Add memory is the page's one action. Reached from Ambrose's ⋮ menu and Settings.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MemoryScreen(
    ui: MemoryUi,
    assistantName: String,
    cb: MemoryCallbacks,
    snackbar: SnackbarHostState = remember { SnackbarHostState() },
    modifier: Modifier = Modifier,
    now: ZonedDateTime = AppClock.now(),
) {
    val sections = remember(ui.active, ui.suggested, ui.query, ui.kind) { MemorySections.of(ui) }
    val nothing = ui.loaded && ui.problem == null && !ui.filtered && ui.active.isEmpty() && ui.suggested.isEmpty()
    Scaffold(
        modifier = modifier,
        contentWindowInsets = WindowInsets(0),
        topBar = {
            TopAppBar(title = { Text(stringResource(R.string.memory_title)) },
                navigationIcon = { IconButton(onClick = cb.back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.memory_back)) } })
        },
        floatingActionButton = {
            // ACT memory.add @android.settings
            ExtendedFloatingActionButton(onClick = cb.add, icon = { Icon(Icons.Outlined.Add, null) },
                text = { Text(stringResource(R.string.memory_add)) }, modifier = Modifier.testTag("memory-add"))
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize(), contentAlignment = Alignment.TopCenter) {
            LazyColumn(Modifier.widthIn(max = 760.dp).fillMaxWidth().testTag("memory-list"), contentPadding = PaddingValues(bottom = 96.dp)) {
                item(key = "desc") {
                    Text(stringResource(R.string.memory_desc, assistantName), style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(start = 20.dp, end = 20.dp, top = 4.dp))
                }
                item(key = "learning") { LearningRow(ui.learning, ui.learnBusy, now, cb.learnNow) }
                item(key = "filters") { Filters(ui, cb) }
                when {
                    !ui.loaded -> item(key = "loading") { Loading() }
                    ui.problem != null && ui.active.isEmpty() && ui.suggested.isEmpty() -> item(key = "problem") { Problem(ui.problem, cb.retry) }
                    nothing -> item(key = "empty") {
                        EmptyBlock(stringResource(R.string.memory_empty_title), stringResource(R.string.memory_empty_hint, assistantName))
                    }
                    ui.filtered && sections.isEmpty -> item(key = "nomatch") {
                        Text(stringResource(R.string.memory_no_match), color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(horizontal = 20.dp, vertical = 16.dp).testTag("memory-no-match"))
                    }
                }
                if (ui.loaded && !nothing) {
                    if (sections.review.isNotEmpty()) reviewItems(sections.review, ui.busy, assistantName, cb)
                    if (sections.about.isNotEmpty() || (!ui.filtered && ui.problem == null)) {
                        item(key = "h-about") { SectionHeader(stringResource(R.string.memory_about_you)) }
                        if (sections.about.isEmpty()) item(key = "about-empty") {
                            Text(stringResource(R.string.memory_about_you_empty), color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp))
                        }
                        items(sections.about, key = { "m-${it.id}" }) { MemoryRow(it, assistantName, now, cb) }
                    }
                    if (sections.people.isNotEmpty()) {
                        item(key = "h-people") { SectionHeader(stringResource(R.string.memory_people)) }
                        sections.people.forEach { g -> groupItems(g, assistantName, now, cb, card = true) }
                    }
                    if (sections.topics.isNotEmpty()) {
                        item(key = "h-topics") { SectionHeader(stringResource(R.string.memory_topics)) }
                        sections.topics.forEach { g -> groupItems(g, assistantName, now, cb, card = false) }
                    }
                }
            }
        }
    }
}

/**
 * The page and, on a wide screen (an unfolded Fold, a tablet), the open person card as a pane at
 * its end, as Calendar shows its detail pane. On a phone the card is a bottom sheet instead.
 */
@Composable
fun MemoryLayout(wide: Boolean, cardOpen: Boolean, page: @Composable (Modifier) -> Unit, pane: @Composable () -> Unit) {
    if (!wide || !cardOpen) {
        page(Modifier.fillMaxSize())
        return
    }
    Row(Modifier.fillMaxSize()) {
        page(Modifier.weight(1f).fillMaxHeight())
        VerticalDivider()
        Surface(Modifier.width(400.dp).fillMaxHeight().testTag("person-pane"), color = MaterialTheme.colorScheme.surfaceContainerLow) { pane() }
    }
}

@Composable
private fun Loading() {
    // The spinner waits a moment, so a quick load shows no flash; its room is kept either way.
    var show by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { kotlinx.coroutines.delay(300); show = true }
    Box(Modifier.fillMaxWidth().heightIn(min = 160.dp), contentAlignment = Alignment.Center) {
        if (show) CircularProgressIndicator(Modifier.testTag("memory-loading"))
    }
}

@Composable
private fun Problem(p: LoadProblem, retry: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 24.dp).testTag("memory-problem"),
        horizontalAlignment = Alignment.CenterHorizontally) {
        if (p == LoadProblem.OFFLINE) {
            Icon(Icons.Outlined.CloudOff, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.size(8.dp))
            Text(stringResource(R.string.memory_offline_title), style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.semantics { heading() })
            Text(stringResource(R.string.memory_offline_hint), color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            Text(stringResource(R.string.memory_load_failed), color = MaterialTheme.colorScheme.error)
        }
        TextButton(onClick = retry) { Text(stringResource(R.string.memory_retry)) }
    }
}

@Composable
private fun EmptyBlock(title: String, hint: String) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 24.dp).testTag("memory-empty")) {
        Text(title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
        Text(hint, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
internal fun SectionHeader(t: String, modifier: Modifier = Modifier) {
    Text(t, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary,
        modifier = modifier.padding(start = 20.dp, end = 20.dp, top = 20.dp, bottom = 4.dp).semantics { heading() })
}

/** "Last learned 6:07 AM · 0 new" (or what went wrong) and Learn now. ACT memory.learning_status @android.settings */
@Composable
private fun LearningRow(s: LearningStatus?, busy: Boolean, now: ZonedDateTime, learnNow: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp).heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
        val last = s?.last
        val text = when {
            s == null -> ""
            !s.enabled -> stringResource(R.string.memory_learning_off)
            last == null -> stringResource(R.string.memory_never_learned)
            last.error != null -> stringResource(R.string.memory_last_failed, learnedWhen(last.at, now), stringResource(learnError(last.error)))
            else -> pluralStringResource(R.plurals.memory_last_learned, last.suggested, learnedWhen(last.at, now), last.suggested)
        }
        // The line keeps its height while it loads, so nothing below it moves.
        Text(text, style = MaterialTheme.typography.bodySmall,
            color = if (last?.error != null) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f).testTag("learning-status"))
        TextButton(onClick = learnNow, enabled = !busy, modifier = Modifier.testTag("learn-now")) {
            Icon(Icons.Outlined.AutoAwesome, null, Modifier.size(18.dp))
            Spacer(Modifier.width(6.dp))
            Text(stringResource(R.string.memory_learn_now))
        }
    }
}

private fun learnError(code: String): Int = when (code) {
    "cut_off" -> R.string.memory_error_cut_off
    "unreadable" -> R.string.memory_error_unreadable
    "gave_up" -> R.string.memory_error_gave_up
    else -> R.string.memory_error_model_failed
}

/** "6:07 AM" today, else "Oct 4, 6:07 AM" (the web's `when`). */
private fun learnedWhen(iso: String, now: ZonedDateTime): String {
    val d = Format.parse(iso)?.withZoneSameInstant(now.zone) ?: return ""
    return if (d.toLocalDate() == now.toLocalDate()) d.format(DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault()))
    else d.format(LocaleFormats.date("MMMdhmma", "MMM d, h:mm a"))
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Filters(ui: MemoryUi, cb: MemoryCallbacks) {
    Column(Modifier.fillMaxWidth()) {
        val searchLabel = stringResource(R.string.memory_search)
        OutlinedTextField(ui.query, cb.setQuery, singleLine = true,
            placeholder = { Text(stringResource(R.string.memory_search)) },
            leadingIcon = { Icon(Icons.Outlined.Search, null) },
            trailingIcon = if (ui.query.isEmpty()) null else ({
                IconButton(onClick = { cb.setQuery("") }) { Icon(Icons.Outlined.Clear, stringResource(R.string.memory_clear_search)) }
            }),
            modifier = Modifier.padding(horizontal = 16.dp).fillMaxWidth().testTag("memory-search").semantics { contentDescription = searchLabel })
        val filterLabel = stringResource(R.string.memory_filter_label)
        // The kind chips: one line that scrolls sideways on a narrow screen, never wrapping, as the
        // app's other chip rows (search, reply suggestions): the row runs to the screen's edges and
        // its padding scrolls with the chips. The scroll follows the layout direction (RTL starts at
        // the right).
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 16.dp).padding(top = 4.dp)
            .testTag("memory-kinds").semantics { contentDescription = filterLabel },
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(selected = ui.kind == null, onClick = { cb.setKind(null) }, label = { Text(stringResource(R.string.memory_filter_all)) },
                leadingIcon = if (ui.kind == null) ({ Icon(Icons.Outlined.Check, null, Modifier.size(FilterChipDefaults.IconSize)) }) else null)
            MemoryKind.entries.forEach { k ->
                val label = stringResource(k.label)
                val example = stringResource(R.string.memory_kind_example, stringResource(k.example))
                // The kind's example: a tooltip on long press, and read out with the chip's name.
                TooltipBox(TooltipDefaults.rememberPlainTooltipPositionProvider(), tooltip = { PlainTooltip { Text(example) } },
                    state = rememberTooltipState()) {
                    FilterChip(selected = ui.kind == k.id, onClick = { cb.setKind(k.id) }, label = { Text(label) },
                        leadingIcon = if (ui.kind == k.id) ({ Icon(Icons.Outlined.Check, null, Modifier.size(FilterChipDefaults.IconSize)) }) else null,
                        modifier = Modifier.testTag("kind-${k.id}").semantics { contentDescription = "$label. $example" })
                }
            }
        }
    }
}

/** Suggestions from mail and chats: Keep or Dismiss, one by one or all at once. */
private fun LazyListScope.reviewItems(items: List<Memory>, busy: Set<Long>, assistantName: String, cb: MemoryCallbacks) {
    item(key = "h-review") {
        Column {
            SectionHeader(stringResource(R.string.memory_to_review))
            Text(stringResource(R.string.memory_to_review_desc), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 20.dp))
            if (items.size > 1) Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), horizontalArrangement = Arrangement.End) {
                TextButton(onClick = { cb.dismiss(items.map { it.id }) }) { Text(stringResource(R.string.memory_dismiss_all)) }
                FilledTonalButton(onClick = { cb.keep(items.map { it.id }) }, modifier = Modifier.testTag("keep-all")) {
                    Icon(Icons.Outlined.Check, null, Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text(stringResource(R.string.memory_keep_all))
                }
            }
        }
    }
    items(items, key = { "s-${it.id}" }) { m -> ReviewRow(m, m.id in busy, assistantName, cb) }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ReviewRow(m: Memory, busy: Boolean, assistantName: String, cb: MemoryCallbacks) {
    val about = m.refs.firstNotNullOfOrNull { it.about }
    val you = stringResource(R.string.memory_you)
    val who = when {
        m.entity != null -> if (m.entity.isSelf) you else m.entity.name
        about?.self == true -> you
        else -> about?.name?.ifBlank { null }
    }
    Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 8.dp).testTag("review-row")) {
        Text(m.text, style = MaterialTheme.typography.bodyLarge)
        FlowRow(Modifier.padding(top = 4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            KindPill(m.kind)
            if (who != null) Pill(who, info = true)
            SourceText(m, assistantName, cb)
            m.confidence?.let { MetaText(stringResource(R.string.memory_sure, Math.round(it * 100).toInt())) }
            m.conversationId?.let { c -> cb.openSource?.let { open -> SourceLink { open(c) } } }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            TextButton(onClick = { cb.dismiss(listOf(m.id)) }, enabled = !busy) { Text(stringResource(R.string.memory_dismiss)) }
            FilledTonalButton(onClick = { cb.keep(listOf(m.id)) }, enabled = !busy) {
                Icon(Icons.Outlined.Check, null, Modifier.size(18.dp))
                Spacer(Modifier.width(6.dp))
                Text(stringResource(R.string.memory_keep))
            }
        }
    }
    HorizontalDivider(Modifier.padding(horizontal = 20.dp), color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f))
}

private fun LazyListScope.groupItems(g: MemoryGroup, assistantName: String, now: ZonedDateTime, cb: MemoryCallbacks, card: Boolean) {
    item(key = "g-${g.entityId}") {
        Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp, top = 8.dp).testTag("memory-group"), verticalAlignment = Alignment.CenterVertically) {
            Text(g.title, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f).semantics { heading() })
            // ACT person.card @android.settings: the person's card (contact details and Ambrose's memory).
            if (card) {
                val label = stringResource(R.string.memory_open_card_of, g.title)
                TextButton(onClick = { cb.openCard(g.entityId) }, modifier = Modifier.semantics { contentDescription = label }) {
                    Icon(Icons.Outlined.Badge, null, Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text(stringResource(R.string.memory_open_card))
                }
            }
        }
    }
    items(g.items, key = { "m-${it.id}" }) { MemoryRow(it, assistantName, now, cb) }
}

/** One memory: its text and what is known about it; a tap edits it; ⋮ holds Create task, Pin and Delete. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun MemoryRow(m: Memory, assistantName: String, now: ZonedDateTime, cb: MemoryCallbacks, showWho: Boolean = false) {
    var menu by remember { mutableStateOf(false) }
    val expired = Format.parse(m.expiresAt)?.isBefore(now) == true
    Row(Modifier.fillMaxWidth().clickable(onClickLabel = stringResource(R.string.memory_edit)) { cb.edit(m) }
        .padding(start = 20.dp, end = 4.dp, top = 8.dp, bottom = 8.dp).testTag("memory-row"), verticalAlignment = Alignment.Top) {
        Column(Modifier.weight(1f)) {
            Text(m.text, style = MaterialTheme.typography.bodyLarge,
                color = if (expired) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
            FlowRow(Modifier.padding(top = 4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                KindPill(m.kind)
                if (m.pinned) Icon(Icons.Outlined.PushPin, stringResource(R.string.memory_pin), Modifier.size(16.dp).align(Alignment.CenterVertically),
                    tint = MaterialTheme.colorScheme.primary)
                if (showWho && m.entity != null) Pill(if (m.entity.isSelf) stringResource(R.string.memory_you) else m.entity.name, info = true)
                SourceText(m, assistantName, cb)
                m.expiresAt?.let { iso ->
                    val day = Format.parse(iso)?.format(LocaleFormats.date("MMMd", "MMM d")).orEmpty()
                    MetaText(stringResource(if (expired) R.string.memory_expired else R.string.memory_until, day))
                }
                if (m.sensitive) {
                    val desc = stringResource(R.string.memory_local_only_desc)
                    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.semantics { contentDescription = desc }) {
                        Icon(Icons.Outlined.Lock, null, Modifier.size(12.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.width(2.dp))
                        MetaText(stringResource(R.string.memory_local_only))
                    }
                }
                m.updatedAt?.let { MetaText(Format.listDate(it, now)) }
                m.conversationId?.let { c -> cb.openSource?.let { open -> SourceLink { open(c) } } }
            }
        }
        Box {
            IconButton(onClick = { menu = true }, modifier = Modifier.testTag("memory-more-${m.id}")) {
                Icon(Icons.Outlined.MoreVert, stringResource(R.string.memory_more_options))
            }
            DropdownMenu(menu, { menu = false }) {
                // ACT memory.make_task @android.settings: a commitment as a task.
                if (m.kind == "commitment") DropdownMenuItem({ Text(stringResource(R.string.memory_make_task)) },
                    { menu = false; cb.makeTask(m) }, leadingIcon = { Icon(Icons.Outlined.TaskAlt, null) })
                DropdownMenuItem({ Text(stringResource(if (m.pinned) R.string.memory_unpin else R.string.memory_pin)) },
                    { menu = false; cb.pin(m) }, leadingIcon = { Icon(Icons.Outlined.PushPin, null) })
                DropdownMenuItem({ Text(stringResource(R.string.memory_edit)) }, { menu = false; cb.edit(m) },
                    leadingIcon = { Icon(Icons.Outlined.Edit, null) })
                DropdownMenuItem({ Text(stringResource(R.string.memory_delete)) }, { menu = false; cb.delete(m) },
                    leadingIcon = { Icon(Icons.Outlined.Delete, null) })
            }
        }
    }
}

@Composable
internal fun sourceLabel(source: String, assistantName: String): String = when (source) {
    "agent" -> assistantName
    "extracted" -> stringResource(R.string.memory_source_mail)
    "chat" -> stringResource(R.string.memory_source_chat)
    else -> stringResource(R.string.memory_source_you)
}

@Composable
internal fun KindPill(kind: String) {
    val k = MemoryKind.of(kind)
    Pill(k?.let { stringResource(it.label) } ?: kind)
}

@Composable
internal fun Pill(text: String, info: Boolean = false) {
    Surface(shape = RoundedCornerShape(50), color = if (info) MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.surfaceContainerHighest) {
        Text(text, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis,
            color = if (info) MaterialTheme.colorScheme.onSecondaryContainer else MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp))
    }
}

@Composable
private fun MetaText(t: String) {
    Text(t, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

/**
 * Where a memory came from. "From a chat" is a link to that chat, at the message it came from, while
 * the chat exists (the API's `chat`); every other source is plain text.
 * ACT memory.open_chat @android.settings
 */
@Composable
private fun SourceText(m: Memory, assistantName: String, cb: MemoryCallbacks) {
    val label = sourceLabel(m.source, assistantName)
    val chat = m.chat?.takeIf { m.source == "chat" }
    val open = cb.openChat
    if (chat == null || open == null) {
        MetaText(label)
        return
    }
    Row(Modifier.clickable(onClickLabel = stringResource(R.string.memory_open_chat)) { open(chat.sessionId, chat.messageId) }
        .testTag("memory-open-chat"), verticalAlignment = Alignment.CenterVertically) {
        Icon(Icons.AutoMirrored.Outlined.Chat, null, Modifier.size(12.dp), tint = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.width(2.dp))
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
    }
}

@Composable
private fun SourceLink(onClick: () -> Unit) {
    Row(Modifier.clickable(onClick = onClick), verticalAlignment = Alignment.CenterVertically) {
        Icon(Icons.AutoMirrored.Outlined.OpenInNew, null, Modifier.size(12.dp), tint = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.width(2.dp))
        Text(stringResource(R.string.memory_source_link), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
    }
}
