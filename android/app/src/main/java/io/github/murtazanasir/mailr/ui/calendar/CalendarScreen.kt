package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import kotlinx.coroutines.flow.drop
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.EnterTransition
import androidx.compose.animation.ExitTransition
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.automirrored.outlined.EventNote
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.outlined.ArrowDropDown
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.CalendarViewDay
import androidx.compose.material.icons.outlined.CalendarViewMonth
import androidx.compose.material.icons.outlined.CalendarViewWeek
import androidx.compose.material.icons.outlined.Event
import androidx.compose.material.icons.outlined.Menu
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material.icons.outlined.Today
import androidx.compose.material.icons.outlined.ViewColumn
import androidx.compose.material.icons.outlined.ViewWeek
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import androidx.compose.material3.DrawerValue
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SheetValue
import androidx.compose.material3.SnackbarDuration
import io.github.murtazanasir.mailr.look.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.material3.VerticalDivider
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.rememberDrawerState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.window.core.layout.WindowWidthSizeClass
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.EventDraft
import io.github.murtazanasir.mailr.data.reminders.QuickChoices
import io.github.murtazanasir.mailr.ui.components.EmptyState
import io.github.murtazanasir.mailr.ui.openUrl
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import io.github.murtazanasir.mailr.data.cal.Zones

/** What the side pane (unfolded) or a sheet (phone) shows. */
sealed interface CalPane {
    data object None : CalPane
    /** [prep]: opened from a reminder's Prep, so the meeting prep card starts open (event.prep). */
    data class Detail(val key: String, val prep: Boolean = false) : CalPane
    data class Edit(val original: CalendarEvent?, val start: LocalDateTime, val allDay: Boolean = false, val seed: QuickSeed? = null) : CalPane
}

/**
 * Open an event from a notification or link (its UID and occurrence), or a view and date from
 * an app link (the web's `day`, `ndays`, `workweek`, `week`, `month`, `schedule`; an ISO date).
 */
data class EventRequest(val uid: String?, val occurrence: String?, val view: String? = null, val date: String? = null,
                        val prep: Boolean = false)

/** The calendar view of a web route segment, or null. */
fun calViewOf(route: String?): CalView? = when (route) {
    "day" -> CalView.DAY
    "ndays" -> CalView.THREE_DAY
    "workweek" -> CalView.WORK_WEEK
    "week" -> CalView.WEEK
    "month" -> CalView.MONTH
    "schedule" -> CalView.AGENDA
    else -> null
}

/** Quick create, opened from the FAB (no slot), a ghost, a long-press range or an empty day. */
private data class QuickRequest(val slot: Pair<LocalDateTime, LocalDateTime>?, val allDay: Boolean = false)

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun CalendarRoute(vm: CalendarViewModel, morningHour: Int, defaultReminder: Int?, request: EventRequest?, consumed: () -> Unit,
                  openEmail: (Long) -> Unit = {}, newTask: (() -> Unit)? = null,
                  /** Opens the composer: to (comma-separated), subject, body. */
                  compose: ((String, String, String) -> Unit)? = null,
                  /** Opens the assistant about [CalendarEvent]. */
                  askAssistant: ((CalendarEvent) -> Unit)? = null) {
    val view by vm.view.collectAsStateWithLifecycle()
    val anchor by vm.anchor.collectAsStateWithLifecycle()
    val range by vm.range.collectAsStateWithLifecycle()
    val events by vm.events.collectAsStateWithLifecycle()
    val connected by vm.connected.collectAsStateWithLifecycle()
    val syncing by vm.syncing.collectAsStateWithLifecycle()
    val collections by vm.collections.collectAsStateWithLifecycle()
    val defaults by vm.defaults.collectAsStateWithLifecycle()
    val now by vm.now.collectAsStateWithLifecycle()
    val selected by vm.selected.collectAsStateWithLifecycle()
    val zone by vm.displayZone.collectAsStateWithLifecycle()
    val pending by vm.pendingKeys.collectAsStateWithLifecycle()
    val waiting by vm.waiting.collectAsStateWithLifecycle()
    val offline by vm.offline.collectAsStateWithLifecycle()
    val taskChips by vm.tasks.collectAsStateWithLifecycle()
    val accounts by vm.accounts.collectAsStateWithLifecycle()
    val lastSynced by vm.lastSynced.collectAsStateWithLifecycle()
    val conflicts by vm.conflicts.collectAsStateWithLifecycle()
    val prompt by vm.prompt.collectAsStateWithLifecycle()
    val taskPrompt by vm.taskPrompt.collectAsStateWithLifecycle()
    val hourHeight by vm.prefs.hourHeight.collectAsStateWithLifecycle()
    val home by vm.prefs.homeZone.collectAsStateWithLifecycle()
    val travel by vm.prefs.travel.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    val wide = currentWindowAdaptiveInfo().windowSizeClass.windowWidthSizeClass != WindowWidthSizeClass.COMPACT
    val drawer = rememberDrawerState(DrawerValue.Closed)
    var pane by remember { mutableStateOf<CalPane>(CalPane.None) }
    var monthPicker by remember { mutableStateOf(false) }
    var jump by remember { mutableStateOf(false) }
    var viewMenu by remember { mutableStateOf(false) }
    var more by remember { mutableStateOf(false) }
    var fabMenu by remember { mutableStateOf(false) }
    var quick by remember { mutableStateOf<QuickRequest?>(null) }
    var searching by remember { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }
    var rescheduleFor by remember { mutableStateOf<CalendarEvent?>(null) }
    var colorFor by remember { mutableStateOf<CalendarEvent?>(null) }
    var dismissedConflicts by remember { mutableStateOf(emptySet<String>()) }

    LaunchedEffect(vm) {
        vm.messages.collect { m ->
            scope.launch {
                val r = snackbar.showSnackbar(m.text, m.actionLabel, withDismissAction = m.actionLabel == null,
                    duration = if (m.actionLabel != null) SnackbarDuration.Long else SnackbarDuration.Short)
                if (r == SnackbarResult.ActionPerformed) m.action?.invoke()
            }
        }
    }
    LaunchedEffect(pane) { vm.select((pane as? CalPane.Detail)?.key) }
    LaunchedEffect(request) {
        val r = request ?: return@LaunchedEffect
        calViewOf(r.view)?.let(vm::setView)
        r.date?.let { d -> catching { LocalDate.parse(d.take(10)) }.getOrNull() }?.let(vm::setAnchor)
        if (r.uid != null) {
            val key = "${r.uid}|${r.occurrence.orEmpty()}"
            val e = vm.instance(key) ?: if (r.occurrence == null) vm.firstInstance(r.uid) else null
            if (e != null) {
                io.github.murtazanasir.mailr.ui.util.Format.parse(e.start)?.toLocalDate()?.let(vm::setAnchor)
                // One window per event: an editor already open on it stays (with what is typed in it).
                if (!editing(pane, r.uid)) pane = CalPane.Detail(e.key, prep = r.prep)
            }
        }
        consumed()
    }
    BackHandler(pane != CalPane.None && wide) { pane = CalPane.None }
    BackHandler(drawer.isOpen) { scope.launch { drawer.close() } }

    val dayEvs = remember(events, collections, zone) { dayEvents(events.orEmpty(), collections, zone) }
    val eventCollections = collections.filter { it.events }
    val places = remember(events) { events.orEmpty().mapNotNull { it.location?.trim()?.takeIf { l -> l.isNotEmpty() && l.length < 60 } }.distinct() }
    val phoneZone = ZoneId.systemDefault()
    val homeZone = Zones.of(home)
    val motion = !reducedMotion(ctx)

    fun quickAt(s: LocalDateTime, e: LocalDateTime, allDay: Boolean = false) { quick = QuickRequest(s to e, allDay) }
    fun fabCreate() {
        // ACT event.create @android.editor @android.calendar: from the FAB, quick create with no time preset (or the shown day's morning).
        quick = if (anchor == AppClock.today(zone)) QuickRequest(null) else QuickRequest(anchor.atTime(morningHour, 0) to anchor.atTime(morningHour + 1, 0))
    }
    val grid = remember(vm) {
        GridActions(
            createRange = { s, e -> quickAt(s, e) },
            move = { e, s, end -> vm.requestTimes(e, s, end) },
            duplicate = { e, s, end -> vm.duplicate(e, s, end) },
            delete = { e -> vm.requestDelete(e) },
            reschedule = { e -> rescheduleFor = e },
            readOnly = { e -> vm.readOnly(e) },
            notify = { m -> scope.launch { snackbar.showSnackbar(m) } },
            completeTask = { t -> vm.completeTask(t) },
            toAllDay = { e, d -> vm.requestAllDay(e, d) },
            toTimed = { e, s -> vm.requestTimed(e, s) },
            rescheduleTask = { t, at, d -> vm.requestTaskDue(t, vm.dueAt(at, d)) },
            moveDays = { e, n -> vm.requestMoveDays(e, n) },
        )
    }

    val drawerContent: @Composable () -> Unit = {
        ModalDrawerSheet(Modifier.widthIn(max = 340.dp)) {
            CalendarDrawer(vm, anchor, range, remember(dayEvs) { busyDays(dayEvs) }, accounts, defaults.calendar,
                onGo = { d -> vm.setAnchor(d); scope.launch { drawer.close() } }, morningHour = morningHour)
        }
    }

    ModalNavigationDrawer(drawerContent = drawerContent, drawerState = drawer, gesturesEnabled = drawer.isOpen) {
    Scaffold(
        contentWindowInsets = WindowInsets(0),
        topBar = {
            TopAppBar(
                navigationIcon = { IconButton(onClick = { scope.launch { drawer.open() } }) { Icon(Icons.Outlined.Menu, stringResource(R.string.cal_menu_cd)) } },
                title = {
                    Column {
                        TextButton(onClick = { monthPicker = true }, contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 4.dp)) {
                            Text(periodTitle(view, anchor), style = MaterialTheme.typography.titleLarge, color = MaterialTheme.colorScheme.onSurface,
                                maxLines = 1)
                            Icon(Icons.Outlined.ArrowDropDown, stringResource(R.string.cal_pick_month_cd))
                        }
                        syncedAgo(lastSynced, now)?.let {
                            Text(stringResource(R.string.cal_synced_refresh, it), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(start = 4.dp))
                        }
                    }
                },
                actions = {
                    if (syncing) CircularProgressIndicator(Modifier.size(18.dp).padding(end = 4.dp), strokeWidth = 2.dp)
                    if (wide && view != CalView.AGENDA) {
                        IconButton(onClick = { vm.step(-1) }) { Icon(Icons.AutoMirrored.Filled.KeyboardArrowLeft, stringResource(R.string.cal_previous)) }
                        IconButton(onClick = { vm.step(1) }) { Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, stringResource(R.string.cal_next)) }
                    }
                    // ACT event.search @android.calendar
                    IconButton(onClick = { searching = true }) { Icon(Icons.Outlined.Search, stringResource(R.string.cal_search_placeholder)) }
                    IconButton(onClick = vm::today) { Icon(Icons.Outlined.Today, stringResource(R.string.rem_day_today)) }
                    Box {
                        IconButton(onClick = { viewMenu = true }) { Icon(viewIcon(view), stringResource(R.string.cal_view_cd, view.label)) }
                        DropdownMenu(viewMenu, { viewMenu = false }) {
                            CalView.entries.forEach { v ->
                                DropdownMenuItem({ Text(v.label) }, { vm.setView(v); viewMenu = false },
                                    leadingIcon = { Icon(viewIcon(v), null) },
                                    trailingIcon = if (v == view) ({ Icon(Icons.Filled.Check, stringResource(R.string.cal_current)) }) else null)
                            }
                        }
                    }
                    Box {
                        IconButton(onClick = { more = true }) { Icon(Icons.Outlined.MoreVert, stringResource(R.string.cal_more)) }
                        DropdownMenu(more, { more = false }) {
                            // ACT calendar.sync @android.calendar
                            DropdownMenuItem({ Text(stringResource(R.string.cal_refresh)) }, { more = false; vm.refresh() })
                        }
                    }
                },
            )
        },
        floatingActionButton = {
            if (connected != false) Box {
                val newEventLabel = stringResource(R.string.cal_quick_new_event_cd)
                // The FAB opens quick create; a long-press chooses Event or Task.
                Surface(shape = RoundedCornerShape(16.dp), color = MaterialTheme.colorScheme.primaryContainer, shadowElevation = 6.dp,
                    modifier = Modifier.clip(RoundedCornerShape(16.dp))
                        .combinedClickable(onClickLabel = newEventLabel, onLongClickLabel = stringResource(R.string.cal_fab_long_label),
                            onLongClick = { fabMenu = true }, onClick = ::fabCreate)
                        .semantics { contentDescription = newEventLabel }) {
                    Row(Modifier.heightIn(min = 56.dp).padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Filled.Add, null, tint = MaterialTheme.colorScheme.onPrimaryContainer)
                        if (view == CalView.AGENDA || wide) {
                            Spacer(Modifier.width(8.dp))
                            Text(stringResource(R.string.cal_fab_event), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onPrimaryContainer)
                        }
                    }
                }
                DropdownMenu(fabMenu, { fabMenu = false }) {
                    DropdownMenuItem({ Text(stringResource(R.string.cal_fab_event)) }, { fabMenu = false; fabCreate() }, leadingIcon = { Icon(Icons.Outlined.Event, null) })
                    if (newTask != null) DropdownMenuItem({ Text(stringResource(R.string.cal_fab_task)) }, { fabMenu = false; newTask() }, leadingIcon = { Icon(Icons.Outlined.TaskAlt, null) })
                    // ACT recording.record @android.calendar: New recording (Recordings (Speakr) on only).
                    io.github.murtazanasir.mailr.ui.recordings.LocalRecordings.current?.let { rec ->
                        DropdownMenuItem({ Text(stringResource(R.string.cal_prep_recording)) }, { fabMenu = false; rec.newRecording(null) },
                            leadingIcon = { Icon(androidx.compose.material.icons.Icons.Outlined.Mic, null) })
                    }
                }
            }
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { pad ->
        Row(Modifier.padding(pad).fillMaxSize()) {
            Column(Modifier.weight(1f).fillMaxHeight()) {
                ReminderBanners(offline = false)
                if (offline) OfflineBanner(waiting)
                if (homeZone != null && homeZone.rules != phoneZone.rules && travel[phoneZone.id] == null)
                    TravelBanner(phoneZone, homeZone) { vm.prefs.setTravel(phoneZone.id, it) }
                accounts.filter { it.error != null }.forEach { a ->
                    AccountErrorBanner(a.name, a.error!!, a.authFailed,
                        onOpenWeb = { openUrl(ctx, vm.graph.sessions.server.trimEnd('/') + "/settings/calendar") }, onRetry = vm::refresh)
                }
                val allHidden = eventCollections.isNotEmpty() && eventCollections.none { it.visible }
                if (allHidden) AllHiddenBanner { vm.showAll() }
                when {
                    // Plain words; the built-in calendar needs no address.
                    connected == false -> EmptyState(Icons.Outlined.CalendarMonth, stringResource(R.string.cal_not_set_up),
                        stringResource(R.string.cal_not_set_up_body),
                        action = stringResource(R.string.cal_check_again) to vm::refresh)
                    events == null -> if (view.grid) TimeGrid(gridDays(view, anchor), emptyList(), now, null, {}, {}, null, zone = zone,
                        hourHeight = hourHeight.dp, skeleton = true) else Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
                    else -> PullToRefreshBox(syncing, vm::refresh, Modifier.fillMaxSize()) {
                        // ACT event.edit @android.calendar: a tap opens the event, whose Edit opens the editor.
                        CalendarBody(view, anchor, dayEvs, now, selected, wide, vm, zone, hourHeight, taskChips, pending, grid, motion,
                            open = { pane = CalPane.Detail(it.key) },
                            createOn = { d -> quickAt(d.atTime(morningHour, 0), d.atTime(morningHour + 1, 0)) })
                    }
                }
            }
            if (wide) {
                VerticalDivider()
                Box(Modifier.widthIn(min = 360.dp, max = 460.dp).fillMaxWidth(0.42f).fillMaxHeight()) {
                    when (val p = pane) {
                        CalPane.None -> DayList(anchor, dayEvs, selected, { pane = CalPane.Detail(it.key) },
                            { quickAt(anchor.atTime(morningHour, 0), anchor.atTime(morningHour + 1, 0)) }, Modifier.fillMaxSize())
                        is CalPane.Detail -> DetailFor(vm, p.key, dayEvs.map { it.e }, collections, morningHour, zone, now,
                            prepOpen = p.prep, onClose = { pane = CalPane.None }, onEdit = { pane = CalPane.Edit(it, AppClock.localNow()) },
                            onDelete = { e -> pane = CalPane.None; vm.requestDelete(e) }, onColor = { colorFor = it },
                            openEmail = openEmail, dismiss = { pane = CalPane.None }, compose = compose, askAssistant = askAssistant)
                        is CalPane.Edit -> EditorFor(vm, p, eventCollections, defaults.calendar, defaultReminder, morningHour, saving,
                            onClose = { pane = CalPane.None }, onDone = { pane = CalPane.None }, onSave = { orig, d, sc, rr, lv ->
                                saving = true
                                vm.save(orig, d, sc, rr, live = lv) { ok -> saving = false; if (ok) pane = CalPane.None }
                            }, onDelete = { e, sc -> pane = CalPane.None; vm.requestDeleteScoped(e, sc) }, onRsvp = vm::rsvp,
                            openEmail = { id -> pane = CalPane.None; openEmail(id) })
                    }
                }
            }
        }
    }
    }

    if (!wide) when (val p = pane) {
        CalPane.None -> Unit
        is CalPane.Detail -> {
            val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = false)
            ModalBottomSheet(onDismissRequest = { pane = CalPane.None }, sheetState = sheet) {
                DetailFor(vm, p.key, dayEvs.map { it.e }, collections, morningHour, zone, now, prepOpen = p.prep, onClose = null,
                    onEdit = { pane = CalPane.Edit(it, AppClock.localNow()) },
                    onDelete = { e -> pane = CalPane.None; vm.requestDelete(e) }, onColor = { colorFor = it },
                    openEmail = { id -> pane = CalPane.None; openEmail(id) }, dismiss = { pane = CalPane.None },
                    compose = compose?.let { c -> { to: String, sub: String, body: String -> pane = CalPane.None; c(to, sub, body) } },
                    askAssistant = askAssistant?.let { a -> { e: CalendarEvent -> pane = CalPane.None; a(e) } })
            }
        }
        is CalPane.Edit -> {
            var confirmDiscard by remember { mutableStateOf(false) }
            var dirty by remember { mutableStateOf(false) }
            val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true, confirmValueChange = { v ->
                if (v == SheetValue.Hidden && dirty) { confirmDiscard = true; false } else true
            })
            ModalBottomSheet(onDismissRequest = { if (dirty) confirmDiscard = true else pane = CalPane.None }, sheetState = sheet) {
                EditorFor(vm, p, eventCollections, defaults.calendar, defaultReminder, morningHour, saving,
                    onDirty = { dirty = it },
                    onClose = { if (dirty) confirmDiscard = true else pane = CalPane.None },
                    onDone = { pane = CalPane.None },
                    onSave = { orig, d, sc, rr, lv ->
                        saving = true
                        vm.save(orig, d, sc, rr, live = lv) { ok -> saving = false; if (ok) pane = CalPane.None }
                    }, onDelete = { e, sc -> pane = CalPane.None; vm.requestDeleteScoped(e, sc) }, onRsvp = vm::rsvp,
                    openEmail = { id -> pane = CalPane.None; openEmail(id) })
            }
            if (confirmDiscard) DiscardDialog({ confirmDiscard = false }) { confirmDiscard = false; pane = CalPane.None }
        }
    }

    quick?.let { q ->
        QuickCreateSheet(vm.quickText, q.slot, q.allDay, eventCollections, defaults.calendar, defaultReminder, morningHour, places, zone,
            conflict = { s, e -> vm.conflictLine(s, e) }, people = vm::people, newUid = vm::newUid,
            onText = { vm.quickText = it }, onDismiss = { quick = null },
            onSave = { d: EventDraft -> quick = null; vm.save(null, d, "all", false) },
            onMore = { seed -> quick = null; pane = CalPane.Edit(null, seed.start, seed.allDay, seed) },
            userDefaults = { allDay -> vm.userDefaultReminders(allDay, defaultReminder) })
    }
    ChangePrompt(prompt, vm)
    taskPrompt?.let { ScopeSheet(stringResource(R.string.cal_scope_change_task), onDismiss = { vm.answerTaskScope(null) }, forTask = true) { sc -> vm.answerTaskScope(sc) } }
    conflicts.firstOrNull { it.key !in dismissedConflicts }?.let { c ->
        ConflictPrompt(c, vm) { dismissedConflicts = dismissedConflicts + c.key }
    }
    if (searching) CalendarSearch(vm, collections, zone, onOpen = { e ->
        searching = false
        io.github.murtazanasir.mailr.ui.util.Format.parse(e.start)?.withZoneSameInstant(zone)?.toLocalDate()?.let(vm::setAnchor)
        pane = CalPane.Detail(e.key)
    }, onClose = { searching = false })
    rescheduleFor?.let { e ->
        // TalkBack "Reschedule…": the when picker with the reschedule presets; the duration is kept.
        val s = io.github.murtazanasir.mailr.ui.util.Format.parse(e.start)?.withZoneSameInstant(zone)
        if (s == null) rescheduleFor = null else {
            val len = java.time.Duration.between(s, io.github.murtazanasir.mailr.ui.util.Format.parse(e.end ?: e.start)?.withZoneSameInstant(zone) ?: s.plusHours(1))
            WhenDialog(stringResource(R.string.cal_move_to), s, QuickChoices.eventReschedule(s), allowNoTime = false, initiallyDateOnly = false,
                onDismiss = { rescheduleFor = null }, onOtherDate = null) { moved, _ ->
                rescheduleFor = null
                val st = moved.withZoneSameInstant(zone).toLocalDateTime()
                vm.requestTimes(e, st, st.plus(len))
            }
        }
    }
    colorFor?.let { e ->
        val col = collections.firstOrNull { it.url == e.calendar }
        ColorSheet(stringResource(R.string.cal_event_colour), e.color?.takeIf { it != e.calendarColor && it != col?.color }, CalendarColors.of(e.calendar, col?.color),
            onDismiss = { colorFor = null }) { hex -> colorFor = null; vm.setEventColor(e, hex) }
    }
    if (monthPicker) MonthPickerDialog(anchor, { monthPicker = false }, onPickDay = { monthPicker = false; jump = true }) {
        monthPicker = false
        vm.setAnchor(it)
    }
    if (jump) DateDialog(anchor, { jump = false }) { vm.setAnchor(it); jump = false }
}

/** After a drop or a delete: the series question (this event, this and following, all), then the question whether to send updates when guests are affected. */
@Composable
internal fun ChangePrompt(prompt: PendingChange?, vm: CalendarViewModel) {
    val c = prompt ?: return
    when (c.step) {
        "scope" -> ScopeSheet(if (c.kind == ChangeKind.DELETE) stringResource(R.string.cal_scope_delete_event) else stringResource(R.string.cal_scope_change_event),
            onDismiss = vm::cancelPrompt, onPick = vm::answerScope)
        "send" -> SendUpdatesSheet(if (c.kind == ChangeKind.DELETE) "cancel" else "update", c.guests, onBack = vm::cancelPrompt,
            onAnswer = vm::answerSend)
    }
}

/** The conflict sheet (changed elsewhere) for the oldest write the server refused. */
@Composable
internal fun ConflictPrompt(c: io.github.murtazanasir.mailr.data.cal.CalConflict, vm: CalendarViewModel, onDismiss: () -> Unit) {
    ConflictSheet(c, onDismiss = onDismiss, onTheirs = { vm.keepTheirs(c) }, onMine = { vm.keepMine(c) }, onBoth = { vm.keepBoth(c) })
}

/** "October", "28 Sep – 4 Oct", "Thu 1 Oct": the period the view shows. */
fun periodTitle(view: CalView, anchor: LocalDate): String {
    val thisYear = anchor.year == AppClock.today().year
    val month = if (thisYear) LocaleFormats.date("MMMM", "MMMM") else LocaleFormats.date("yMMMM", "MMMM yyyy")
    return when (view) {
        CalView.DAY -> anchor.format(if (thisYear) LocaleFormats.date("EEEdMMM", "EEE d MMM") else LocaleFormats.date("yMMMEEEd", "EEE d MMM yyyy"))
        CalView.THREE_DAY, CalView.WORK_WEEK, CalView.WEEK -> {
            val days = gridDays(view, anchor)
            val a = days.first()
            val b = days.last()
            if (a.month == b.month) a.format(month)
            else "${a.format(LocaleFormats.date("MMM", "MMM"))} – ${b.format(if (thisYear) LocaleFormats.date("MMM", "MMM") else LocaleFormats.date("yMMM", "MMM yyyy"))}"
        }
        else -> anchor.format(month)
    }
}

private fun viewIcon(v: CalView) = when (v) {
    CalView.AGENDA -> Icons.AutoMirrored.Outlined.EventNote
    CalView.DAY -> Icons.Outlined.CalendarViewDay
    CalView.THREE_DAY -> Icons.Outlined.ViewColumn
    CalView.WORK_WEEK -> Icons.Outlined.ViewWeek
    CalView.WEEK -> Icons.Outlined.CalendarViewWeek
    CalView.MONTH -> Icons.Outlined.CalendarViewMonth
}

@Composable
private fun CalendarBody(
    view: CalView, anchor: LocalDate, events: List<DayEvent>, now: Long, selected: String?, wide: Boolean, vm: CalendarViewModel,
    zone: ZoneId, hourHeight: Float, tasks: List<TaskChip>, pending: Set<String>, grid: GridActions, motion: Boolean,
    open: (CalendarEvent) -> Unit, createOn: (LocalDate) -> Unit,
) {
    if (view == CalView.AGENDA) {
        AgendaView(events, anchor, now, selected, open, createOn, pending)
        return
    }
    var drag by remember { mutableStateOf(0f) }
    var direction by remember { mutableStateOf(1) }
    val previousLabel = stringResource(R.string.cal_previous)
    val nextLabel = stringResource(R.string.cal_next)
    // Swipe sideways to change period.
    val swipe = Modifier.pointerInput(view) {
        detectHorizontalDragGestures(
            onDragStart = { drag = 0f },
            onDragEnd = {
                if (kotlin.math.abs(drag) > 80.dp.toPx()) { direction = if (drag < 0) 1 else -1; vm.step(direction) }
                drag = 0f
            },
        ) { _, dx -> drag += dx }
    }.semantics {
        customActions = listOf(
            CustomAccessibilityAction(previousLabel) { direction = -1; vm.step(-1); true },
            CustomAccessibilityAction(nextLabel) { direction = 1; vm.step(1); true },
        )
    }
    AnimatedContent(targetState = anchor to view, modifier = Modifier.fillMaxSize().then(swipe), label = "cal", // i18n-ignore: animation label (debug tools)
        transitionSpec = {
            when {
                !motion -> EnterTransition.None togetherWith ExitTransition.None
                initialState.second != targetState.second -> fadeIn() togetherWith fadeOut()
                else -> (slideInHorizontally { w -> w / 3 * direction } + fadeIn()) togetherWith (slideOutHorizontally { w -> -w / 3 * direction } + fadeOut())
            }
        }) { (a, v) ->
        when (v) {
            CalView.DAY, CalView.THREE_DAY, CalView.WORK_WEEK, CalView.WEEK ->
                TimeGrid(gridDays(v, a), events, now, selected, open, { createOn(it.toLocalDate()) },
                    if (v == CalView.DAY) null else ({ d: LocalDate -> vm.setAnchor(d); vm.setView(CalView.DAY) }),
                    zone = zone, hourHeight = hourHeight.dp, onZoom = { vm.prefs.setHourHeight(it.value) }, tasks = tasks, pending = pending,
                    actions = grid)
            CalView.MONTH -> if (wide) MonthView(a, events, true, vm::setAnchor, { vm.setAnchor(it); vm.setView(CalView.DAY) }, Modifier.fillMaxSize(),
                actions = grid)
            else Column(Modifier.fillMaxSize()) {
                // Note #14: the grid takes most of the screen (titles in the days); the picked day's agenda, dense, below.
                MonthView(a, events, false, vm::setAnchor, { vm.setAnchor(it); vm.setView(CalView.DAY) }, Modifier.weight(0.64f), actions = grid)
                HorizontalDivider()
                DayList(a, events, selected, open, { createOn(a) }, Modifier.weight(0.36f), compact = true)
            }
            CalView.AGENDA -> Unit
        }
    }
}

@Composable
private fun DetailFor(vm: CalendarViewModel, key: String, loaded: List<CalendarEvent>, collections: List<CalendarCollection>,
                      morningHour: Int, zone: ZoneId, now: Long, prepOpen: Boolean = false, onClose: (() -> Unit)?, onEdit: (CalendarEvent) -> Unit,
                      onDelete: (CalendarEvent) -> Unit, onColor: (CalendarEvent) -> Unit, openEmail: (Long) -> Unit = {},
                      dismiss: () -> Unit = {}, compose: ((String, String, String) -> Unit)? = null,
                      askAssistant: ((CalendarEvent) -> Unit)? = null) {
    val ctx = LocalContext.current
    val recordings = io.github.murtazanasir.mailr.ui.recordings.LocalRecordings.current
    val e by produceState(loaded.firstOrNull { it.key == key }, key, loaded) {
        value = loaded.firstOrNull { it.key == key } ?: vm.instance(key)
    }
    // The last copy seen, so an event deleted elsewhere can come back as a new one.
    var last by remember(key) { mutableStateOf<CalendarEvent?>(null) }
    LaunchedEffect(e) { e?.let { last = it } }
    val ev = e ?: return DeletedElsewhere(last, onRestore = { vm.restoreAsNew(it); dismiss() }, onClose = onClose)
    val master by produceState<CalendarEvent?>(null, ev.uid) { if (ev.recurring) value = vm.master(ev.uid) }
    EventDetail(ev, master, collections, morningHour, onClose, { onEdit(ev) }, { onDelete(ev) }, { vm.rsvp(ev, it) },
        onOpenEmail = ev.conversationId?.let { id -> { openEmail(id) } },
        zone = zone, now = now, readOnly = vm.readOnly(ev),
        onRsvpNote = { r, note -> vm.rsvp(ev, r, note) },
        onRsvpScoped = { r, note, sc -> vm.rsvp(ev, r, note, sc) },
        onMove = { url -> vm.moveToCalendar(ev, url) },
        onEmailGuests = compose?.let { c -> { which: String ->
            val m = vm.emailGuests(ev, which)
            if (m.to.isEmpty()) vm.say(AppText.get(R.string.cal_no_guests_to_email)) else c(m.to.joinToString(","), m.subject, m.body)
        } },
        onAskAssistant = askAssistant?.let { a -> { a(ev) } },
        // ACT event.prep @android.calendar: the meeting prep card; its links open the conversation, the task or the recording.
        prep = remember(ev.uid, prepOpen, recordings) {
            PrepHooks(load = vm::prep, summarise = vm::prepSummary, openConversation = openEmail,
                openTask = { uid -> vm.openTaskLink(ctx, uid) },
                openRecording = { cid, rid -> recordings?.show(cid, rid) }, initiallyOpen = prepOpen)
        },
        onReminders = { vm.setReminders(ev, it) },
        onDuplicate = { vm.duplicate(ev) },
        onColor = { onColor(ev) },
        onCopyLink = {
            (ctx.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager)
                ?.setPrimaryClip(ClipData.newPlainText(ev.summary, vm.eventLink(ev)))
        })
}

/** The editor open in [pane] is on the event [uid] (one window per event: opening it again keeps that editor). */
internal fun editing(pane: CalPane, uid: String): Boolean = (pane as? CalPane.Edit)?.original?.uid == uid

@Composable
internal fun EditorFor(
    vm: CalendarViewModel, p: CalPane.Edit, collections: List<CalendarCollection>, defaultCalendar: String?,
    defaultReminder: Int?, morningHour: Int, saving: Boolean, onDirty: (Boolean) -> Unit = {},
    onClose: () -> Unit, onSave: (CalendarEvent?, EventDraft, String, Boolean, io.github.murtazanasir.mailr.data.cal.LiveSave?) -> Unit,
    onDelete: (CalendarEvent, String) -> Unit, onDone: () -> Unit = onClose,
    onRsvp: (CalendarEvent, String) -> Unit = { _, _ -> }, openEmail: (Long) -> Unit = {},
) {
    // A recurring event's RRULE lives on the series master: load it first (offline: none).
    val master by produceState<Pair<Boolean, CalendarEvent?>>(false to null, p) {
        value = true to (p.original?.takeIf { it.recurring }?.let { vm.master(it.uid) })
    }
    if (p.original?.recurring == true && !master.first) {
        Box(Modifier.fillMaxWidth().padding(48.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        return
    }
    val zone by vm.displayZone.collectAsStateWithLifecycle()
    val state = remember(p) {
        EventEditorState(p.original, master.second, p.start, defaultCalendar, defaultReminder, p.original?.uid ?: vm.newUid(), seed = p.seed,
            displayZone = zone, defaultsFor = vm::defaultRemindersOf, userDefaults = { allDay -> vm.userDefaultReminders(allDay, defaultReminder) })
            .also { if (p.allDay && !it.allDay) it.toggleAllDay(true) }
    }
    LaunchedEffect(state) { androidx.compose.runtime.snapshotFlow { state.dirty }.collect(onDirty) }
    // Live editing (data/live): changes others make while the editor is open come in; Save sends the
    // version the editor is based on. The event keeps its explicit Save (guests may get mail).
    val g = vm.graph
    val port = remember(state) { if (state.isNew) null else EventLivePort(state) }
    val live = rememberLiveSession(g, "event", p.original?.uid, port, rebased = { port?.rebased(it) })
    val liveUi = live?.ui?.collectAsStateWithLifecycle()
    LaunchedEffect(state, live) {
        val session = live ?: return@LaunchedEffect
        androidx.compose.runtime.snapshotFlow { port?.mine() }.drop(1).collect { session.typed() }
    }
    fun liveSave() = if (live != null && port != null)
        io.github.murtazanasir.mailr.data.cal.LiveSave(live.baseVersion, port.baseline, onRefused = { live.refused(it) }) else null
    EventEditor(state, collections.filter { !it.readOnly || it.url == p.original?.calendar }, morningHour, saving, onClose,
        onSave = { scope ->
            // ACT event.rsvp @android.editor: the answer goes on its own call; the fields only when they changed.
            val o = p.original
            state.responseChanged?.let { r -> if (o != null) onRsvp(o, r) }
            val move = state.moveTo
            when {
                o == null -> onSave(null, state.draft(), scope, state.recurrenceTouched, null)
                // ACT event.move_calendar @android.editor: the fields are written in the old calendar, then the series moves.
                move != null -> vm.save(o, state.draft().copy(calendar = o.calendar), scope, state.recurrenceTouched,
                    patch = state.otherFieldsDirty, moveTo = move, live = liveSave()) { ok -> if (ok) onDone() }
                state.fieldsDirty -> onSave(o, state.draft(), scope, state.recurrenceTouched, liveSave())
                else -> onDone()
            }
        },
        onDelete = p.original?.let { o -> { scope: String -> onDelete(o, scope) } },
        onOpenEmail = p.original?.conversationId?.let { id -> { openEmail(id) } },
        people = vm::people, findTime = vm::findTime, freebusy = vm::freebusy,
        onDuplicate = p.original?.let { o -> { onDone(); vm.duplicate(o) } },
        live = liveUi?.let { u -> {
            // ACT event.live_update @android.editor, ACT event.resolve_conflict @android.editor
            io.github.murtazanasir.mailr.ui.components.LiveBars(u.value, "event", io.github.murtazanasir.mailr.ui.components.LiveBarActions(
                undo = { live.undo() }, dismiss = { live.dismissNotice() }, useMine = { live.useMine() }, useTheirs = { live.useTheirs() }))
        } })
}

@Composable
fun DiscardDialog(onKeep: () -> Unit, onDiscard: () -> Unit) {
    io.github.murtazanasir.mailr.look.AlertDialog(onDismissRequest = onKeep, title = { Text(stringResource(R.string.cal_discard_title)) },
        text = { Text(stringResource(R.string.cal_discard_body)) },
        confirmButton = { TextButton(onClick = onDiscard) { Text(stringResource(R.string.cal_discard)) } },
        dismissButton = { TextButton(onClick = onKeep) { Text(stringResource(R.string.cal_keep_editing)) } })
}
