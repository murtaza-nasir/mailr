package io.github.murtazanasir.mailr.data.repo

import io.github.murtazanasir.mailr.util.catching

import androidx.paging.ExperimentalPagingApi
import androidx.paging.LoadType
import androidx.paging.Pager
import androidx.paging.PagingConfig
import androidx.paging.PagingData
import androidx.paging.PagingState
import androidx.paging.RemoteMediator
import androidx.paging.map
import io.github.murtazanasir.mailr.data.api.ActionRequest
import io.github.murtazanasir.mailr.data.api.ActionResponse
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.ConversationDetail
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.InboxCount
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.Me
import io.github.murtazanasir.mailr.data.api.ShowInTabChoices
import io.github.murtazanasir.mailr.data.api.MessageBody
import io.github.murtazanasir.mailr.data.db.BodyEntity
import io.github.murtazanasir.mailr.data.db.ConversationEntity
import io.github.murtazanasir.mailr.data.db.KvEntity
import io.github.murtazanasir.mailr.data.db.ListItemEntity
import io.github.murtazanasir.mailr.data.db.MailrDb
import io.github.murtazanasir.mailr.data.db.PendingOpEntity
import io.github.murtazanasir.mailr.data.db.PendingState
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.db.RemoteKeyEntity
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.io.IOException
import java.util.UUID
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/** What a list shows: a view, optional search, optional Priority Inbox section. */
data class MailView(
    val view: String = "inbox",
    val q: String? = null,
    val section: String? = null,
    val sections: String? = null,
    val category: String? = null,
    val title: String = AppText.get(R.string.mail_folder_inbox),
    /** A layout section; the server resolves its definition. */
    val layout: String? = null,
    val tab: String? = null,
    val sec: String? = null,
) {
    val key: String get() = listOf(view, q.orEmpty(), section.orEmpty(), sections.orEmpty(), category.orEmpty(),
        layout.orEmpty(), tab.orEmpty(), sec.orEmpty()).joinToString("|")
    val isInbox: Boolean get() = view == "inbox" || view.startsWith("category:")
}

/** Result of a user action, with what the UI needs for "Undo". */
data class ActionOutcome(val affected: Int, val undo: ActionRequest?, val queuedOffline: Boolean)

/**
 * The single source of truth for mail on the device.
 *
 * Lists come from Room (fed by a RemoteMediator), so they render instantly and offline.
 * Actions are applied to the cache first (optimistic), sent at once with an idempotency key,
 * and queued in `pending_ops` when the device is offline; [OutboxRunner] replays them.
 */
@OptIn(ExperimentalPagingApi::class)
class MailRepository(val api: MailrApi, private val db: MailrDb) {
    /** The user label the mailbox list is showing (set by the list; see contextLabelId). */
    @Volatile var contextLabelId: Long? = null


    private val _labels = MutableStateFlow<List<Label>>(emptyList())
    val labels: StateFlow<List<Label>> = _labels.asStateFlow()

    private val _me = MutableStateFlow<Me?>(null)
    val me: StateFlow<Me?> = _me.asStateFlow()

    /** The number by Inbox (GET /inbox/count, ACT settings.inbox_count @android.drawer); null until the server
     *  answers, and on a server without it (then the Inbox label's unread count shows). */
    private val _inboxCount = MutableStateFlow<InboxCount?>(null)
    val inboxCount: StateFlow<InboxCount?> = _inboxCount.asStateFlow()

    /** Lists re-query the server when this ticks (SSE / change feed / pull-to-refresh). */
    private val _invalidations = MutableSharedFlow<Set<String>>(extraBufferCapacity = 8)
    val invalidations: SharedFlow<Set<String>> = _invalidations

    val pendingCount: Flow<Int> = db.pending().count()

    /** The searches (MailView.key) whose list holds offline results ([OfflineSearch]), not the server's. */
    private val _offlineResults = kotlinx.coroutines.flow.MutableStateFlow<Set<String>>(emptySet())
    val offlineResults: kotlinx.coroutines.flow.StateFlow<Set<String>> = _offlineResults

    /**
     * A search the server could not answer (no network): the matches among the conversations this
     * phone has cached, kept as the search's list until the server answers.
     */
    suspend fun searchOffline(view: MailView): Int {
        val q = view.q?.takeIf { it.isNotBlank() } ?: return 0
        // A LIKE on the longest word narrows the rows decoded; the filter does the rest.
        val word = OfflineSearch.parse(q).words.maxByOrNull { it.length }
        val like = if (word == null) "%" else "%" + word.replace("%", "").replace("_", "") + "%"
        val cached = db.lists().cachedMatching(view.key, like).mapNotNull {
            catching { MailrJson.decodeFromString(ConversationSummary.serializer(), it.json) }.getOrNull()
        }
        val found = OfflineSearch.search(cached, q)
        val rows = found.mapIndexed { i, c -> ListItemEntity(view.key, c.id, i.toLong(), MailrJson.encodeToString(ConversationSummary.serializer(), c)) }
        // refreshedAt 0: the next look asks the server again.
        db.lists().replacePage(view.key, rows, RemoteKeyEntity(view.key, null, found.size, 0))
        _offlineResults.value = _offlineResults.value + view.key
        return found.size
    }

    fun pager(view: MailView, pageSize: Int = 40): Flow<PagingData<ConversationSummary>> = Pager(
        config = PagingConfig(pageSize = pageSize, prefetchDistance = pageSize / 2, initialLoadSize = pageSize,
            enablePlaceholders = false),
        remoteMediator = ConversationMediator(view, pageSize),
        pagingSourceFactory = { db.lists().paging(view.key) },
    ).flow.map { data -> data.map { MailrJson.decodeFromString(ConversationSummary.serializer(), it.json) } }

    inner class ConversationMediator(private val view: MailView, private val pageSize: Int) :
        RemoteMediator<Int, ListItemEntity>() {

        override suspend fun initialize(): InitializeAction {
            val k = db.lists().key(view.key)
            val fresh = k != null && System.currentTimeMillis() - k.refreshedAt < 60_000  // clock-ok: cache age
            return if (fresh) InitializeAction.SKIP_INITIAL_REFRESH else InitializeAction.LAUNCH_INITIAL_REFRESH
        }

        override suspend fun load(loadType: LoadType, state: PagingState<Int, ListItemEntity>): MediatorResult {
            return try {
                val cursor = when (loadType) {
                    LoadType.REFRESH -> null
                    LoadType.PREPEND -> return MediatorResult.Success(endOfPaginationReached = true)
                    LoadType.APPEND -> db.lists().key(view.key)?.nextCursor
                        ?: return MediatorResult.Success(endOfPaginationReached = true)
                }
                val page = api.conversations(view.view, view.q, view.section, view.sections, cursor, pageSize, view.category, view.layout, view.tab, view.sec)
                val start = if (loadType == LoadType.REFRESH) 0L else (db.lists().maxPosition(view.key) ?: -1L) + 1
                val rows = page.items.mapIndexed { i, c ->
                    ListItemEntity(view.key, c.id, start + i, MailrJson.encodeToString(ConversationSummary.serializer(), c))
                }
                val key = RemoteKeyEntity(view.key, page.nextCursor, page.total, System.currentTimeMillis())  // clock-ok: cache stamp
                if (loadType == LoadType.REFRESH) db.lists().replacePage(view.key, rows, key)
                else db.lists().appendPage(rows, key)
                if (view.key in _offlineResults.value) _offlineResults.value = _offlineResults.value - view.key
                MediatorResult.Success(endOfPaginationReached = page.nextCursor == null)
            } catch (e: IOException) {
                // Offline, a search answers from the phone's cached lists (labelled in the list).
                if (loadType == LoadType.REFRESH && !view.q.isNullOrBlank()) {
                    searchOffline(view)
                    return MediatorResult.Success(endOfPaginationReached = true)
                }
                MediatorResult.Error(e)
            } catch (e: ApiException) {
                MediatorResult.Error(e)
            }
        }
    }

    suspend fun total(view: MailView): Int? = db.lists().key(view.key)?.total

    /** A short, non-paged list (Priority Inbox's upper sections). Cached for offline start. */
    suspend fun section(view: MailView, limit: Int = 25): List<ConversationSummary> = sectionPage(view, limit).first

    /** A capped list (an inbox section's top rows) and how many the whole section holds; offline,
     *  the cached rows and the total stored with them. */
    suspend fun sectionPage(view: MailView, limit: Int = 25): Pair<List<ConversationSummary>, Int> {
        return try {
            val page = api.conversations(view.view, view.q, view.section, view.sections, null, limit, view.category, view.layout, view.tab, view.sec)
            val rows = page.items.mapIndexed { i, c ->
                ListItemEntity(view.key, c.id, i.toLong(), MailrJson.encodeToString(ConversationSummary.serializer(), c))
            }
            db.lists().replacePage(view.key, rows, RemoteKeyEntity(view.key, null, page.total, System.currentTimeMillis()))  // clock-ok: cache stamp
            page.items to (page.total ?: page.items.size)
        } catch (e: IOException) {
            val cached = db.lists().first(view.key, limit).map { MailrJson.decodeFromString(ConversationSummary.serializer(), it.json) }
            cached to (db.lists().key(view.key)?.total ?: cached.size)
        }
    }

    fun invalidate(entities: Set<String> = setOf("conversation")) {
        _invalidations.tryEmit(entities)
    }

    // ---------------- account ----------------

    suspend fun refreshMe(): Me = api.me().also {
        _me.value = it
        refreshInboxCount()  // the setting or the layout may have changed on the web
    }

    /** Reads the Inbox count again; a failure keeps the last one. */
    suspend fun refreshInboxCount() {
        val c = try { api.inboxCount() } catch (e: CancellationException) { throw e } catch (e: Exception) { return }
        _inboxCount.value = c
    }

    /**
     * Saves the drawer's Favorites (the `nav_favorites` setting). The new list shows
     * at once; a refusal puts the old one back and throws. ACT nav.favorite_add @android.drawer,
     * ACT nav.favorite_remove @android.drawer, ACT nav.favorite_reorder @android.drawer
     */
    suspend fun saveFavorites(keys: List<String>) = saveSetting("nav_favorites", JsonArray(keys.map(::JsonPrimitive)))

    /**
     * Saves one setting the drawer changes (Favorites, an account's pinned folders, an open More
     * folders). The new value shows at once; a refusal puts the old one back and throws.
     */
    suspend fun saveSetting(key: String, value: kotlinx.serialization.json.JsonElement) {
        val before = _me.value?.settings?.get(key)
        _me.value?.let { m -> _me.value = m.copy(settings = JsonObject(m.settings + (key to value))) }
        val saved = try {
            api.patchSettings(JsonObject(mapOf(key to value)))
        } catch (e: Exception) {
            _me.value?.let { m ->
                val back = if (before == null) m.settings - key else m.settings + (key to before)
                _me.value = m.copy(settings = JsonObject(back))
            }
            throw e
        }
        _me.value?.let { m -> _me.value = m.copy(settings = JsonObject(m.settings + saved)) }
    }

    /**
     * Reads the labels from the server, keeping a copy for offline starts; offline (or on a server
     * error) the last labels stand, or the copy when there are none yet. Cancellation propagates.
     */
    suspend fun refreshLabels(): List<Label> {
        val fresh = try { api.labels() } catch (e: CancellationException) { throw e } catch (e: Exception) { null }
        if (fresh != null) {
            _labels.value = fresh
            unlessClosed { db.cache().put(KvEntity("labels", MailrJson.encodeToString(fresh))) }
            refreshInboxCount()  // it moves with the label counts
            return fresh
        }
        if (_labels.value.isEmpty()) {
            unlessClosed { db.cache().get("labels") }?.let { cached ->
                _labels.value = MailrJson.decodeFromString(cached)
            }
        }
        return _labels.value
    }

    /**
     * Runs a read or write of the offline copy. When the database closed while it ran (the process
     * shutting down, or a test ending), the copy is skipped and null returned. A failure while the
     * database is still open is a real error and propagates, as does cancellation.
     */
    private suspend fun <T> unlessClosed(block: suspend () -> T): T? = try {
        block()
    } catch (e: CancellationException) {
        throw e
    } catch (e: IllegalStateException) {
        if (db.isOpen) throw e else null
    } catch (e: android.database.SQLException) {
        if (db.isOpen) throw e else null
    }

    /**
     * "New folder" in an account's group (ACT mail.account_folder_create @android.drawer): the label is
     * made with the account as its home, and the server pins it to that account's group. The labels and
     * the settings (the pins) are read again, so the drawer shows it at once.
     */
    suspend fun createFolder(name: String, homeAccountId: Long): Label {
        val made = api.createLabel(name, homeAccountId)
        _labels.value = (_labels.value.filter { it.id != made.id } + made)
        refreshLabels()
        try { refreshMe() } catch (e: CancellationException) { throw e } catch (_: Exception) { }
        return made
    }

    fun labelById(id: Long): Label? = _labels.value.firstOrNull { it.id == id }
    fun roleLabel(role: String): Label? = _labels.value.firstOrNull { it.role == role }

    // ---------------- conversation ----------------

    fun conversationFlow(id: Long): Flow<ConversationDetail?> = db.cache().conversationFlow(id).map { e ->
        e?.let { MailrJson.decodeFromString(ConversationDetail.serializer(), it.json) }
    }

    suspend fun refreshConversation(id: Long): ConversationDetail {
        val d = api.conversation(id)
        db.cache().putConversation(ConversationEntity(id, MailrJson.encodeToString(ConversationDetail.serializer(), d),
            System.currentTimeMillis()))  // clock-ok: cache stamp
        db.cache().trimConversations(300)
        return d
    }

    suspend fun body(messageId: Long, imagesAllowed: Boolean, quote: String? = null): MessageBody {
        if (quote == null) {
            db.cache().body(messageId, imagesAllowed)?.let {
                return MailrJson.decodeFromString(MessageBody.serializer(), it.json)
            }
        }
        val b = api.body(messageId, imagesAllowed, quote)
        if (quote == null) {
            db.cache().putBody(BodyEntity(messageId, imagesAllowed, MailrJson.encodeToString(MessageBody.serializer(), b),
                System.currentTimeMillis()))  // clock-ok: cache stamp
            db.cache().trimBodies(400)
        }
        return b
    }

    // ---------------- actions ----------------

    /**
     * Apply an action to conversations: optimistic cache update, then the server call. Offline,
     * the call is queued (same idempotency key) and the optimistic state stands.
     */
    suspend fun act(ids: List<Long>, action: String, labelIds: List<Long>? = null, until: String? = null,
                    currentView: MailView? = null, contextLabelId: Long? = null,
                    tab: String? = null, category: String? = null): ActionOutcome {
        if (ids.isEmpty()) return ActionOutcome(0, null, false)
        val req = ActionRequest(ids = ids, action = action, labelIds = labelIds, until = until,
            labelId = if (action == "move") labelIds?.firstOrNull() else null,
            fromLabelId = if (action == "archive") contextLabelId else null, tab = tab, category = category)
        applyOptimistic(ids, action, labelIds, currentView)
        val key = UUID.randomUUID().toString()
        val out = try {
            val resp: ActionResponse = api.conversationAction(req, key)
            ActionOutcome(resp.affected, undoOf(resp, ids), queuedOffline = false)
        } catch (e: IOException) {
            enqueue("POST", "conversations/actions", MailrJson.encodeToString(req), key, PendingScopes.describe(action, ids.size))
            ActionOutcome(ids.size, null, queuedOffline = true)
        }
        // The conversation is dealt with on this phone: its new-mail notification goes.
        if (action in HANDLED) catching { onHandled?.invoke(ids) }
        return out
    }

    /** The tabs "Show in tab" offers, and the current one (ACT mail.show_in_tab @android.list @android.reader). */
    suspend fun showInTabChoices(ids: List<Long>): ShowInTabChoices = api.showInTabChoices(ids)

    /** Set by the app graph: cancels the new-mail notifications of conversations acted on. */
    var onHandled: ((List<Long>) -> Unit)? = null

    private val HANDLED = setOf("read", "archive", "trash", "spam", "delete_forever", "mute", "snooze", "move")

    /**
     * The server's undo object, kept whole: Undo sends it back unchanged (as the web does), with the
     * conversation ids added only when it names none. ACT mail.important @android.list @android.reader (its undo: restore_importance)
     */
    internal fun undoOf(resp: ActionResponse, ids: List<Long>): ActionRequest? {
        val u = resp.undo ?: return null
        val full = if ("ids" in u) u else kotlinx.serialization.json.JsonObject(u + ("ids" to kotlinx.serialization.json.JsonArray(
            ids.map { kotlinx.serialization.json.JsonPrimitive(it) })))
        return catching { MailrJson.decodeFromJsonElement(ActionRequest.serializer(), full).copy(raw = full) }.getOrNull()
    }

    suspend fun undo(req: ActionRequest): Boolean = try {
        val key = UUID.randomUUID().toString()
        if (req.raw != null) api.client.send("POST", "conversations/actions", ActionResponse.serializer(), MailrJson.encodeToString(
            kotlinx.serialization.json.JsonObject.serializer(), req.raw), key)
        else api.conversationAction(req, key)
        invalidate()
        true
    } catch (e: Exception) {
        false
    }

    /**
     * One optimistic update at a time. Each reads the cached rows, then writes them back; interleaved,
     * the mark-read of a just-opened conversation could write back the row an archive had removed.
     */
    private val optimistic = Mutex()

    private suspend fun applyOptimistic(ids: List<Long>, action: String, labelIds: List<Long>?, currentView: MailView?) =
        optimistic.withLock { applyOptimisticLocked(ids, action, labelIds, currentView) }

    private suspend fun applyOptimisticLocked(ids: List<Long>, action: String, labelIds: List<Long>?, currentView: MailView?) {
        val rows = db.lists().rowsFor(ids)
        val inbox = roleLabel("inbox")?.id
        val updated = mutableListOf<ListItemEntity>()
        for (row in rows) {
            val c = MailrJson.decodeFromString(ConversationSummary.serializer(), row.json)
            val viewName = row.viewKey.substringBefore('|')
            val drop = when (action) {
                "archive", "snooze", "mute" -> viewName == "inbox" || viewName.startsWith("category:")
                "trash" -> viewName != "trash"
                "spam" -> viewName != "spam"
                "delete_forever" -> true
                "untrash" -> viewName == "trash"
                "inbox" -> viewName == "archive"
                "not_spam" -> viewName == "spam"
                "unstar" -> viewName == "starred"
                "remove_labels", "move" -> labelIds.orEmpty().any { viewName == "label:$it" } ||
                    (action == "move" && viewName == "inbox")
                "not_important" -> viewName == "important"
                else -> false
            }
            if (drop) {
                db.lists().remove(row.viewKey, listOf(c.id))
                continue
            }
            val next = when (action) {
                "read" -> c.copy(unreadCount = 0)
                "unread" -> c.copy(unreadCount = maxOf(1, c.unreadCount))
                "star" -> c.copy(starred = true)
                "unstar" -> c.copy(starred = false)
                "important" -> c.copy(important = true)
                "not_important" -> c.copy(important = false)
                "add_labels" -> c.copy(labelIds = (c.labelIds + labelIds.orEmpty()).distinct())
                "remove_labels" -> c.copy(labelIds = c.labelIds - labelIds.orEmpty().toSet())
                "archive" -> c.copy(labelIds = c.labelIds.filter { it != inbox })
                "inbox" -> if (inbox != null) c.copy(labelIds = (c.labelIds + inbox).distinct()) else c
                else -> c
            }
            if (next != c) updated += row.copy(json = MailrJson.encodeToString(ConversationSummary.serializer(), next))
        }
        if (updated.isNotEmpty()) db.lists().upsert(updated)
        // Keep an open conversation's cached copy in step for flag-type actions.
        if (action in setOf("read", "unread", "star", "unstar", "important", "not_important")) {
            ids.forEach { id ->
                db.cache().conversation(id)?.let { e ->
                    val d = MailrJson.decodeFromString(ConversationDetail.serializer(), e.json)
                    val c = d.conversation
                    val nc = when (action) {
                        "read" -> c.copy(unreadCount = 0)
                        "unread" -> c.copy(unreadCount = maxOf(1, c.unreadCount))
                        "star" -> c.copy(starred = true)
                        "unstar" -> c.copy(starred = false)
                        "important" -> c.copy(important = true)
                        else -> c.copy(important = false)
                    }
                    val msgs = when (action) {
                        "read" -> d.messages.map { it.copy(seen = true) }
                        "star" -> d.messages.mapIndexed { i, m -> if (i == d.messages.lastIndex) m.copy(flagged = true) else m }
                        "unstar" -> d.messages.map { it.copy(flagged = false) }
                        else -> d.messages
                    }
                    db.cache().putConversation(e.copy(json = MailrJson.encodeToString(ConversationDetail.serializer(),
                        d.copy(conversation = nc, messages = msgs))))
                }
            }
        }
    }

    // ---------------- offline queue ----------------

    suspend fun enqueue(method: String, path: String, body: String, key: String, description: String) {
        db.pending().insert(PendingOpEntity(idempotencyKey = key, method = method, path = path, body = body,
            description = description, createdAt = System.currentTimeMillis()))  // clock-ok: queue timing (backoff)
        onQueued?.invoke()
    }

    /** Set by the app graph to schedule the outbox worker. */
    var onQueued: (() -> Unit)? = null

    /**
     * Told about a queued operation the server refused for good. True when the refusal is dealt
     * with (a calendar 409 kept as a conflict, a 404 for something gone), and the operation goes;
     * otherwise it stays in Pending changes as failed.
     */
    var onRejected: (suspend (PendingOpEntity, ApiException) -> Boolean)? = null

    /** Every queued and failed change, for Pending changes. */
    val pendingOps: Flow<List<PendingOpEntity>> = db.pending().all()

    /** True after the server answered 401 while the queue drained: it waits, whole, for sign-in. */
    private val _queuePaused = MutableStateFlow(false)
    val queuePaused: StateFlow<Boolean> = _queuePaused.asStateFlow()

    /** A queued change the server refused, or one that kept failing (a snackbar says so). */
    private val _queueFailures = MutableSharedFlow<PendingOpEntity>(extraBufferCapacity = 8)
    val queueFailures: SharedFlow<PendingOpEntity> = _queueFailures

    enum class DrainResult { DONE, RETRY, PAUSED }

    private val drainLock = Mutex()

    /** For tests: the clock the queue's backoff reads. */
    internal var clock: () -> Long = System::currentTimeMillis  // clock-ok: queue backoff; tests set their own

    /**
     * Send queued operations in order. A network failure stops the pass (the device is offline);
     * a 401 pauses the queue until sign-in and drops nothing, and a 426 (this app version is too old
     * for the server) pauses it the same way until the app is updated; a server failure on one change puts
     * it back with a backoff and holds only the changes that depend on it (the same conversations,
     * or the calendar's own sequence), so unrelated ones still go. A change refused for good, or
     * failing [MAX_ATTEMPTS] times, turns FAILED and waits in Pending changes.
     */
    suspend fun drain(): DrainResult = drainLock.withLock {
        _queuePaused.value = false
        val ops = db.pending().queued()
        if (ops.isEmpty()) return@withLock DrainResult.DONE
        val blocked = mutableSetOf<String>()
        var waiting = false
        var changed = false
        try {
            for (op in ops) {
                val scopes = PendingScopes.of(op)
                if (scopes.any { it in blocked } || op.nextAttemptAt > clock()) {
                    blocked += scopes
                    waiting = true
                    continue
                }
                if (PendingScopes.elapsedSnooze(op, clock())) {
                    // Its time passed while it waited: the phone has reminded the user already.
                    db.pending().delete(op.id)
                    changed = true
                    continue
                }
                try {
                    api.client.send(op.method, op.path, kotlinx.serialization.json.JsonElement.serializer(), op.body, op.idempotencyKey)
                    db.pending().delete(op.id)
                    changed = true
                } catch (e: IOException) {
                    db.pending().noteError(op.id, e.message ?: AppText.get(R.string.sync_offline))
                    return@withLock DrainResult.RETRY
                } catch (e: ApiException) {
                    when {
                        e.code == 401 -> {
                            _queuePaused.value = true
                            return@withLock DrainResult.PAUSED
                        }
                        // The server refuses this app version (426): every change waits, unsent and
                        // uncounted, until an update (or a lowered floor) lets them through (AppUpdates).
                        e.code == 426 -> return@withLock DrainResult.PAUSED
                        e.retryable && op.attempts + 1 < MAX_ATTEMPTS -> {
                            db.pending().retryLater(op.id, e.userMessage(), clock() + backoff(op.attempts + 1))
                            blocked += scopes
                            waiting = true
                        }
                        else -> {
                            val handled = !e.retryable && catching { onRejected?.invoke(op, e) == true }.getOrDefault(false)
                            if (handled) db.pending().delete(op.id) else {
                                db.pending().markFailed(op.id, e.userMessage())
                                _queueFailures.tryEmit(op.copy(state = PendingState.FAILED, lastError = e.userMessage()))
                            }
                            changed = true
                        }
                    }
                }
            }
        } finally {
            if (changed) invalidate()
        }
        if (waiting) DrainResult.RETRY else DrainResult.DONE
    }

    /** True when nothing is left to retry (failed changes wait for the user, not the network). */
    suspend fun drainPending(): Boolean = drain() == DrainResult.DONE

    /** Pending changes → Retry: the change goes back in the queue with a fresh count. */
    suspend fun retryPending(id: Long) {
        db.pending().requeue(id)
        onQueued?.invoke()
    }

    /**
     * Pending changes → Discard: the change is forgotten. Lists read the server again, so what
     * the change had already shown on this phone goes away.
     */
    suspend fun discardPending(id: Long) {
        val op = db.pending().get(id) ?: return
        db.pending().delete(id)
        invalidate(setOf("conversation", "label"))
        onDiscarded?.invoke(op)
    }

    /** Set by the app graph: a discarded calendar change syncs the calendar again. */
    var onDiscarded: ((PendingOpEntity) -> Unit)? = null

    // ---------------- sync cursor ----------------

    suspend fun changeCursor(): Long = db.cache().get("changes_cursor")?.toLongOrNull() ?: 0L
    suspend fun setChangeCursor(v: Long) = db.cache().put(KvEntity("changes_cursor", v.toString()))
    suspend fun kv(key: String): String? = db.cache().get(key)
    suspend fun putKv(key: String, value: String) = db.cache().put(KvEntity(key, value))

    companion object {
        /** Server failures (5xx, 408, 429) a change survives before it turns FAILED. */
        const val MAX_ATTEMPTS = 8

        /** 15 s, 30 s, 1 min… up to 30 min between tries of one change. */
        fun backoff(attempt: Int): Long = (15_000L shl (attempt - 1).coerceIn(0, 7)).coerceAtMost(30 * 60_000L)
    }
}
