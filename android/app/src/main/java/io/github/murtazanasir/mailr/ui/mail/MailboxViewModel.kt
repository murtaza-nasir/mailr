package io.github.murtazanasir.mailr.ui.mail

import io.github.murtazanasir.mailr.util.catching

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.paging.PagingData
import androidx.paging.cachedIn
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.ActionRequest
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.InboxLayout
import io.github.murtazanasir.mailr.data.api.ShowInTabTarget
import io.github.murtazanasir.mailr.data.api.LayoutTab
import io.github.murtazanasir.mailr.data.api.LayoutSection
import io.github.murtazanasir.mailr.data.api.LayoutsResponse
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.Me
import io.github.murtazanasir.mailr.data.api.TabCounts
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.SwipeAction
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.CancellationException
import java.time.ZonedDateTime
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/** A transient message with an optional action ("Undo", "Retry"). */
data class UiMessage(val text: String, val actionLabel: String? = null, val action: (suspend () -> Unit)? = null)

/** A capped inbox section above the tab's main list: its top rows, how many it holds, its cap,
 *  and whether "Show more" grew it in place. */
data class Section(val key: String, val title: String, val items: List<ConversationSummary>, val total: Int = items.size,
                   val cap: Int = items.size, val expanded: Boolean = false)

/** How far "Show more" grows a capped section (the web grows it to a page). */
const val SECTION_GROWN = 50

/**
 * The user label a list is built on, by id: a label's own view, or an inbox tab whose sections
 * all read one label (the "Later" tab, whatever its name). Archive there also removes it, and
 * the rows leave its chip off.
 */
internal fun contextLabelOf(v: MailView, l: InboxLayout?): Long? {
    if (v.view.startsWith("label:") && v.q.isNullOrBlank()) return v.view.removePrefix("label:").toLongOrNull()
    if (v.view != "inbox" || !v.q.isNullOrBlank() || v.category != null) return null
    val tab = l?.tabs?.firstOrNull { it.id == v.tab } ?: l?.tabs?.firstOrNull()
    val bases = tab?.sections?.map { it.base }?.toSet().orEmpty()
    return bases.singleOrNull()?.takeIf { it.startsWith("label:") }?.removePrefix("label:")?.toLongOrNull()
}


class MailboxViewModel(private val g: AppGraph) : ViewModel() {

    private val _view = MutableStateFlow(MailView())
    val view: StateFlow<MailView> = _view.asStateFlow()

    val labels: StateFlow<List<Label>> = g.mail.labels
    val me: StateFlow<Me?> = g.mail.me
    val prefs: StateFlow<Prefs> = g.settings.prefs.stateIn(viewModelScope, SharingStarted.Eagerly, Prefs())
    val pending: StateFlow<Int> = g.mail.pendingCount.stateIn(viewModelScope, SharingStarted.Eagerly, 0)

    /** Pending changes: the queue and the composer's local copies, failed first. */
    val pendingChanges: StateFlow<List<PendingChange>> = combine(g.mail.pendingOps, g.drafts.all, g.mail.queuePaused) { ops, drafts, paused ->
        PendingChange.list(ops, drafts, paused)
    }.stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())
    val queuePaused: StateFlow<Boolean> = g.mail.queuePaused

    private val _pendingOpen = MutableStateFlow(false)
    val pendingOpen: StateFlow<Boolean> = _pendingOpen.asStateFlow()
    fun showPending(open: Boolean) { _pendingOpen.value = open }

    /** Pending changes → Retry. */
    fun retryPending(id: Long) { viewModelScope.launch { g.mail.retryPending(id) } }

    /** Pending changes → Discard. */
    fun discardPending(id: Long) {
        viewModelScope.launch {
            g.mail.discardPending(id)
            _refresh.tryEmit(Unit)
            loadSections()
        }
    }

    /** Pending changes → Discard on a draft only this phone has. */
    fun discardLocalDraft(id: String) {
        viewModelScope.launch {
            val draftId = g.drafts.get(id)?.draftId
            g.drafts.delete(id)
            if (draftId != null) catching { g.compose.discardDraft(draftId) }
        }
    }

    /** Where the list is: Spam and Trash have their own actions. */
    val place: StateFlow<MailPlace> = _view.map { MailPlace.ofView(it.view) }
        .stateIn(viewModelScope, SharingStarted.Eagerly, MailPlace.OTHER)

    /** Conversations waiting for the Delete forever confirmation (a swipe or the selection bar). */
    private val _confirmDelete = MutableStateFlow<List<Long>?>(null)
    val confirmDelete: StateFlow<List<Long>?> = _confirmDelete.asStateFlow()
    fun askDeleteForever(ids: List<Long>) { if (ids.isNotEmpty()) _confirmDelete.value = ids }
    fun cancelDeleteForever() { _confirmDelete.value = null }

    /** ACT mail.delete_forever @android.list, after the confirmation. */
    fun confirmDeleteForever() {
        val ids = _confirmDelete.value ?: return
        _confirmDelete.value = null
        act(ids, "delete_forever")
    }

    /** Inbox layouts: the server resolves every inbox type, built in or custom. */
    private val _layouts = MutableStateFlow<LayoutsResponse?>(null)
    val layout: StateFlow<InboxLayout?> = _layouts.map { it?.activeLayout }
        .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    private val _tabCounts = MutableStateFlow<Map<String, Int>>(emptyMap())
    val tabCounts: StateFlow<Map<String, Int>> = _tabCounts.asStateFlow()
    // What each tab shows by its name (unread, all, due) for InboxTabs.
    private val _tabIndicators = MutableStateFlow<Map<String, TabCounts>>(emptyMap())
    val tabIndicators: StateFlow<Map<String, TabCounts>> = _tabIndicators.asStateFlow()

    /** The inbox tab a view shows (the first tab unless one was picked). */
    private fun tabOf(v: MailView, l: InboxLayout?): LayoutTab? =
        l?.tabs?.firstOrNull { it.id == v.tab } ?: l?.tabs?.firstOrNull()

    /** "Show only this section" (ACT layout.section_focus @android.list): the tab shows that section
     *  alone as its paged list, until Back, another tab or another view. */
    private data class Focus(val tab: String, val sec: String)
    private val _focus = MutableStateFlow<Focus?>(null)
    /** Capped sections that "Show more" grew in place (ACT layout.section_more @android.list). */
    private val _grown = MutableStateFlow<Set<String>>(emptySet())

    private fun focusedSec(v: MailView, l: InboxLayout?, f: Focus?): LayoutSection? {
        val tab = tabOf(v, l) ?: return null
        if (f == null || f.tab != tab.id || tab.sections.size < 2 || !v.isInboxHome()) return null
        return tab.sections.firstOrNull { it.id == f.sec }
    }

    /** The section shown alone, if any (the list's header then has Back). */
    val focusedSection: StateFlow<String?> = combine(_view, layout, _focus) { v, l, f -> focusedSec(v, l, f)?.id }
        .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /** The tab's main section (its header has "Show only this section" too). */
    val mainSection: StateFlow<String?> = combine(_view, layout) { v, l -> if (v.isInboxHome()) tabOf(v, l)?.main?.id else null }
        .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    fun focusSection(sec: String) {
        val tab = tabOf(_view.value, layout.value) ?: return
        _focus.value = Focus(tab.id, sec)
    }

    fun unfocusSection() { _focus.value = null }

    fun toggleGrown(sec: String) {
        _grown.value = _grown.value.let { if (sec in it) it - sec else it + sec }
    }

    /** The list actually paged: on the inbox, the tab's main section, or the section shown alone. */
    private val pagedView: Flow<MailView> = combine(_view, layout, _focus) { v, l, f ->
        val tab = tabOf(v, l)
        val main = focusedSec(v, l, f) ?: tab?.main
        if (!v.isInboxHome() || l == null || tab == null || main == null) v
        else v.copy(layout = l.id, tab = tab.id, sec = main.id)
    }.distinctUntilChanged()

    val conversations: Flow<PagingData<ConversationSummary>> =
        pagedView.flatMapLatest { g.mail.pager(it) }.cachedIn(viewModelScope)

    private val _sections = MutableStateFlow<List<Section>>(emptyList())
    val sections: StateFlow<List<Section>> = _sections.asStateFlow()

    /** Header over the paged list when a tab has several sections. */
    val sectionTitle: StateFlow<String?> = combine(_view, layout, _focus) { v, l, f ->
        val tab = tabOf(v, l)
        focusedSec(v, l, f)?.let { return@combine it.title }
        // A one-section tab shows its name too when another tab has several, so the top of the
        // list stays the same between tabs (as on the web).
        if (!v.isInboxHome() || l == null || tab == null) null
        else if (tab.sections.size < 2 && l.tabs.none { it.sections.size > 1 }) null else tab.main?.title
    }.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /** The user label behind the list ([contextLabelOf]); its chip is left off the rows. */
    val contextLabel: StateFlow<Long?> = combine(_view, layout) { v, l -> contextLabelOf(v, l) }
        .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /** The drawer's Favorites; null until the settings have loaded. */
    val favorites: StateFlow<List<String>?> = combine(me, labels) { m, ls ->
        m?.let { resolveFavorites(storedFavorites(it.settings), ls) }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /** The drawer's account groups: pinned folders, open More folders, the option (AccountFolderPins.kt). */
    val accountFolders: StateFlow<AccountFolderPrefs> = me.map { accountFolderPrefs(it?.settings) }
        .stateIn(viewModelScope, SharingStarted.Eagerly, AccountFolderPrefs())

    /** Messages about Favorites, shown inside the open drawer (the list's snackbar sits under its scrim). */
    private val _favoriteMessages = MutableSharedFlow<UiMessage>(extraBufferCapacity = 4)
    val favoriteMessages: SharedFlow<UiMessage> = _favoriteMessages

    private val _selection = MutableStateFlow<Set<Long>>(emptySet())
    val selection: StateFlow<Set<Long>> = _selection.asStateFlow()

    private val _messages = MutableSharedFlow<UiMessage>(extraBufferCapacity = 4)
    val messages: SharedFlow<UiMessage> = _messages

    /** Ticks when the visible list should re-query (server changes, pull-to-refresh). */
    private val _refresh = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val refresh: SharedFlow<Unit> = _refresh

    private val _refreshing = MutableStateFlow(false)
    val refreshing: StateFlow<Boolean> = _refreshing.asStateFlow()

    init {
        // A queued change the server refused, or one that kept failing: say so, with the way to it.
        viewModelScope.launch {
            g.mail.queueFailures.collect { op ->
                _messages.emit(UiMessage(AppText.get(R.string.mail_couldnt_sync, io.github.murtazanasir.mailr.data.repo.PendingScopes.title(op)), AppText.get(R.string.mail_details)) {
                    _pendingOpen.value = true
                })
            }
        }
        viewModelScope.launch { catching { g.mail.refreshMe() } }
        viewModelScope.launch { g.mail.refreshLabels() }
        viewModelScope.launch {
            g.mail.invalidations.collect { ents ->
                if ("conversation" in ents || "message" in ents) {
                    _refresh.tryEmit(Unit)
                    loadSections()
                }
            }
        }
        viewModelScope.launch {
            combine(_view, layout, _focus, _grown) { _, _, _, _ -> }.collect {
                g.mail.contextLabelId = contextLabelId()
                loadSections()
            }
        }
        // Settings (the active inbox type, custom layouts) change: reload the layouts.
        viewModelScope.launch {
            me.map { it?.settings?.get("inbox_type")?.toString() + "|" + it?.settings?.get("inbox_layouts")?.toString() + "|" +
                it?.settings?.get("priority_sections")?.toString() + "|" + it?.settings?.get("categories")?.toString() }
                .distinctUntilChanged().collect { loadLayouts() }
        }
    }

    private suspend fun loadLayouts() {
        catching { g.mail.api.layouts() }.onSuccess { _layouts.value = it }
        loadTabCounts()
    }

    private suspend fun loadTabCounts() {
        val l = layout.value ?: return
        if (l.tabs.size < 2 || l.tabs.all { it.badge == "none" && it.indicators.isNullOrEmpty() }) {
            _tabCounts.value = emptyMap()
            _tabIndicators.value = emptyMap()
            return
        }
        catching { g.mail.api.layoutCounts(l.id) }.onSuccess { _tabCounts.value = it }
        catching { g.mail.api.layoutIndicators(l.id) }.onSuccess { _tabIndicators.value = it }
    }

    /** The user label the current list shows (a label view, or a layout tab built on one
     *  label such as "Later"): Archive there also removes it. */
    fun contextLabelId(): Long? = contextLabelOf(_view.value, layout.value)

    /** Archive or Move to Inbox for these rows, by their own state ([archiveSlot]). */
    fun archiveSlotOf(rows: List<ConversationSummary>): ArchiveSlot =
        archiveSlot(rows, MailPlace.ofView(_view.value.view), labels.value.associateBy { it.id }, contextLabelId(),
            archiveRemovesLabel(me.value?.settings))

    /** Reads the settings again: Favorites changed on the web show on resume and when the drawer opens. */
    fun refreshMe() {
        viewModelScope.launch { catching { g.mail.refreshMe() } }
    }

    // ---------- favorites ----------

    /** The list edits start from: the shown list, or the stored one while the labels load. */
    private fun favoritesBase(): List<String> {
        val stored = storedFavorites(me.value?.settings)
        return if (labels.value.isEmpty()) (stored ?: DEFAULT_FAVORITES).distinct() else resolveFavorites(stored, labels.value)
    }

    /** ACT nav.favorite_add @android.drawer: long-press a folder or label in the drawer, Add to Favorites. */
    fun addFavorite(key: String) = saveFavorites(io.github.murtazanasir.mailr.ui.mail.addFavorite(favoritesBase(), key), AppText.get(R.string.mail_favorites_added))

    /** ACT nav.favorite_remove @android.drawer: long-press, Remove from Favorites. */
    fun removeFavorite(key: String) = saveFavorites(io.github.murtazanasir.mailr.ui.mail.removeFavorite(favoritesBase(), key), AppText.get(R.string.mail_favorites_removed))

    /** ACT nav.favorite_reorder @android.drawer: long-press a favorite, Move up or Move down. */
    fun moveFavorite(key: String, delta: Int) = saveFavorites(io.github.murtazanasir.mailr.ui.mail.moveFavorite(favoritesBase(), key, delta), null)

    private fun saveFavorites(next: List<String>, message: String?) {
        val prev = favoritesBase()
        if (next == prev) return
        viewModelScope.launch {
            try {
                g.mail.saveFavorites(next)
                if (message != null) _favoriteMessages.emit(UiMessage(message, AppText.get(R.string.mail_undo)) {
                    try { g.mail.saveFavorites(prev) } catch (e: Exception) { _favoriteMessages.emit(UiMessage(AppText.get(R.string.mail_couldnt_undo))) }
                })
            } catch (e: Exception) {
                _favoriteMessages.emit(UiMessage(AppText.get(R.string.mail_favorites_save_failed, e.userMessage()), AppText.get(R.string.mail_retry)) { saveFavorites(next, message) })
            }
        }
    }

    // ---------- an account group's pinned folders (AccountFolderPins.kt) ----------

    /** ACT mail.account_folder_pin @android.drawer: long-press a folder under More folders, Pin to <address>. */
    fun pinFolder(accountId: Long, labelId: Long, address: String) {
        val p = accountFolders.value.pins
        savePins(io.github.murtazanasir.mailr.ui.mail.pinFolder(p, accountId, labelId), p, AppText.get(R.string.mail_pinned_to, address))
    }

    /** ACT mail.account_folder_unpin @android.drawer: long-press a pinned folder, Unpin. */
    fun unpinFolder(accountId: Long, labelId: Long) {
        val p = accountFolders.value.pins
        savePins(io.github.murtazanasir.mailr.ui.mail.unpinFolder(p, accountId, labelId), p, AppText.get(R.string.mail_unpinned))
    }

    /** ACT mail.account_folder_reorder @android.drawer: long-press a pinned folder, Move up or Move down. */
    fun movePinnedFolder(accountId: Long, labelId: Long, delta: Int, among: List<Long>) {
        val p = accountFolders.value.pins
        savePins(movePin(p, accountId, labelId, delta, among), p, null)
    }

    /**
     * ACT mail.account_folder_create @android.drawer: "New folder" from an account's heading, or "New folder
     * inside" from a folder's long press ([name] is the full path). The server makes it on the account and
     * pins it to the group; a refusal throws, and the dialog shows why.
     */
    suspend fun createFolder(accountId: Long, name: String): Label = g.mail.createFolder(name, accountId)

    /** ACT mail.account_folders_more @android.drawer: tap More folders. */
    fun setMoreFoldersOpen(accountId: Long, open: Boolean) {
        val now = accountFolders.value.moreOpen
        val next = if (open) now + accountId else now - accountId
        if (next == now) return
        viewModelScope.launch {
            try {
                g.mail.saveSetting("account_folders_more_open", openJson(next))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _favoriteMessages.emit(UiMessage(AppText.get(R.string.mail_pins_save_failed, e.userMessage())))
            }
        }
    }

    private fun savePins(next: Map<Long, List<Long>>, prev: Map<Long, List<Long>>, message: String?) {
        if (next == prev) return
        viewModelScope.launch {
            try {
                g.mail.saveSetting("account_folder_pins", pinsJson(next))
                if (message != null) _favoriteMessages.emit(UiMessage(message, AppText.get(R.string.mail_undo)) {
                    try { g.mail.saveSetting("account_folder_pins", pinsJson(prev)) } catch (e: Exception) { _favoriteMessages.emit(UiMessage(AppText.get(R.string.mail_couldnt_undo))) }
                })
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _favoriteMessages.emit(UiMessage(AppText.get(R.string.mail_pins_save_failed, e.userMessage()), AppText.get(R.string.mail_retry)) { savePins(next, prev, message) })
            }
        }
    }

    private fun MailView.isInboxHome() = view == "inbox" && q.isNullOrBlank() && category == null

    private suspend fun loadSections() {
        val v = _view.value
        val l = layout.value
        val tab = tabOf(v, l)
        val main = tab?.main
        if (!v.isInboxHome() || l == null || tab == null || main == null || tab.sections.size < 2 ||
            focusedSec(v, l, _focus.value) != null) {
            _sections.value = emptyList()
            loadTabCounts()
            return
        }
        val grown = _grown.value
        _sections.value = tab.sections.filter { it.id != main.id }.map { s ->
            val cap = s.limit ?: 10
            val more = s.id in grown
            val (rows, total) = g.mail.sectionPage(MailView("inbox", layout = l.id, tab = tab.id, sec = s.id),
                if (more) maxOf(cap, SECTION_GROWN) else cap)
            Section(s.id, s.title, rows, total, cap, more)
        }
        loadTabCounts()
    }

    fun open(view: MailView) {
        _selection.value = emptySet()
        // Another tab or view: all sections again, at their caps.
        if (view.tab != _view.value.tab || view.view != _view.value.view || view.q != _view.value.q) {
            _focus.value = null
            _grown.value = emptySet()
        }
        _view.value = view
    }

    fun pullToRefresh() {
        viewModelScope.launch {
            _refreshing.value = true
            catching { g.mail.refreshLabels(); loadSections() }
            _refresh.tryEmit(Unit)
            kotlinx.coroutines.delay(600)
            _refreshing.value = false
        }
    }

    // ---------- selection ----------

    fun toggle(id: Long) {
        _selection.value = _selection.value.let { if (id in it) it - id else it + id }
    }

    fun clearSelection() {
        _selection.value = emptySet()
    }

    fun selectAll(ids: List<Long>) {
        _selection.value = ids.toSet()
    }

    // ---------- actions ----------

    /**
     * The list's actions (selection bar, row star, swipes) and the reader's Snooze / Label sheets
     * all land here: ACT mail.archive @android.list, ACT mail.delete @android.list, ACT mail.read @android.list, ACT mail.unread @android.list, ACT mail.star @android.list,
     * ACT mail.snooze @android.reader, ACT mail.label @android.reader (add_labels / remove_labels).
     */
    fun act(ids: List<Long>, action: String, labelIds: List<Long>? = null, until: String? = null,
            tab: String? = null, category: String? = null, message: String? = null) {
        if (ids.isEmpty()) return
        _selection.value = emptySet()
        viewModelScope.launch {
            try {
                val out = g.mail.act(ids, action, labelIds, until, _view.value, contextLabelId(), tab, category)
                val msg = message ?: describe(action, ids.size, out.queuedOffline)
                val undo: ActionRequest? = out.undo
                _messages.emit(if (undo != null) UiMessage(msg, AppText.get(R.string.mail_undo)) {
                    if (!g.mail.undo(undo)) _messages.emit(UiMessage(AppText.get(R.string.mail_couldnt_undo)))
                    else _refresh.tryEmit(Unit)
                } else UiMessage(msg))
                // Which tab lists the conversation changed: the paged list reloads too.
                if (action == "show_in_tab" || action == "category") _refresh.tryEmit(Unit)
                if (out.undo != null || action in setOf("archive", "trash", "spam", "snooze", "move", "not_spam", "untrash", "delete_forever")) {
                    // The optimistic change already shows; a quiet refresh reconciles it.
                    loadSections()
                }
            } catch (e: Exception) {
                _messages.emit(UiMessage(e.userMessage(), AppText.get(R.string.mail_retry)) { act(ids, action, labelIds, until, tab, category, message) })
                _refresh.tryEmit(Unit)
            }
        }
    }

    /**
     * Show in tab: the conversations show in [t] of the active layout, in one server action with
     * Undo (ACT mail.show_in_tab @android.list @android.reader). The list reloads, as the row
     * leaves the tab it was in.
     */
    fun showInTab(ids: List<Long>, t: ShowInTabTarget) {
        act(ids, "show_in_tab", tab = t.id, message = TabMenus.shownIn(t.name, ids.size))
    }

    /** The raw category, from More → Category (ACT mail.category @android.list @android.reader). */
    fun setCategory(ids: List<Long>, category: String) {
        act(ids, "category", category = category, message = AppText.get(R.string.mail_category_set, TabMenus.categoryName(category)))
    }

    /**
     * Row swipes on the list: ACT mail.archive, ACT mail.delete, ACT mail.read, ACT mail.unread, ACT mail.star.
     * In Trash the archive swipe restores (ACT mail.untrash @android.list) and the delete swipe asks to delete
     * forever (ACT mail.delete_forever @android.list); in Spam the archive swipe is Not spam (ACT mail.not_spam @android.list).
     */
    fun swipe(c: ConversationSummary, action: SwipeAction, snooze: () -> Unit) {
        // ACT mail.move @android.list (Move to Inbox): the archive swipe on a row not in the Inbox.
        if (action == SwipeAction.ARCHIVE && archiveSlotOf(listOf(c)) == ArchiveSlot.INBOX) return act(listOf(c.id), "inbox")
        when (MailPlace.ofView(_view.value.view)) {
            MailPlace.TRASH -> when (action) {
                SwipeAction.ARCHIVE -> return act(listOf(c.id), "untrash")
                SwipeAction.TRASH -> return askDeleteForever(listOf(c.id))
                else -> Unit
            }
            MailPlace.SPAM -> if (action == SwipeAction.ARCHIVE) return act(listOf(c.id), "not_spam")
            MailPlace.OTHER -> Unit
        }
        when (action) {
            SwipeAction.ARCHIVE -> act(listOf(c.id), "archive")
            SwipeAction.TRASH -> act(listOf(c.id), "trash")
            SwipeAction.READ -> act(listOf(c.id), if (c.unread) "read" else "unread")
            SwipeAction.STAR -> act(listOf(c.id), if (c.starred) "unstar" else "star")
            SwipeAction.SNOOZE -> snooze()
            SwipeAction.NONE -> Unit
        }
    }

    fun snooze(ids: List<Long>, at: ZonedDateTime) = act(ids, "snooze", until = at.toOffsetDateTime().toString())

    private fun describe(action: String, n: Int, offline: Boolean): String {
        val done = when (action) {
            "archive" -> R.plurals.mail_done_archive
            "trash" -> R.plurals.mail_done_trash
            "spam" -> R.plurals.mail_done_spam
            "not_spam" -> R.plurals.mail_done_not_spam
            "read" -> R.plurals.mail_done_read
            "unread" -> R.plurals.mail_done_unread
            "star" -> R.plurals.mail_done_star
            "unstar" -> R.plurals.mail_done_unstar
            "snooze" -> R.plurals.mail_done_snooze
            "mute" -> R.plurals.mail_done_mute
            "important" -> R.plurals.mail_done_important
            "not_important" -> R.plurals.mail_done_not_important
            "add_labels", "remove_labels" -> R.plurals.mail_done_labels
            "move" -> R.plurals.mail_done_move
            "untrash" -> R.plurals.mail_done_untrash
            "inbox" -> R.plurals.mail_done_inbox
            "delete_forever" -> R.plurals.mail_done_delete_forever
            else -> R.plurals.mail_done_other
        }
        return AppText.plural(done, n, n) + if (offline) AppText.get(R.string.mail_will_sync_online) else ""
    }

    fun postMessage(m: UiMessage) {
        _messages.tryEmit(m)
    }
}
