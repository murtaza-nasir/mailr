package io.github.murtazanasir.mailr.ui.approvals

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.ApprovalPreview
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.OutboxRow
import io.github.murtazanasir.mailr.data.api.activeOutbox
import io.github.murtazanasir.mailr.data.api.notSent
import io.github.murtazanasir.mailr.data.api.retrySend
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.sync.Notifier
import io.github.murtazanasir.mailr.data.sync.NotifyDecisions
import io.github.murtazanasir.mailr.data.sync.ServerEvent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import java.util.WeakHashMap

/** A decided approval for the history list (GET /approvals with the decided statuses). */
@Serializable
data class ApprovalRecord(
    val id: Long,
    val actor: String = "",
    val action: String = "",
    val target: String = "",
    val preview: ApprovalPreview = ApprovalPreview(),
    val status: String = "",
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("decided_at") val decidedAt: String? = null,
    @SerialName("executed_at") val executedAt: String? = null,
    val result: JsonElement? = null,
) {
    /** The outcome in a word or two: "Done", "Denied", "Expired", "Failed", "Running". */
    val outcome: String
        get() = when (status) {
            "executed" -> AppText.get(R.string.appr_outcome_done)
            "approved", "executing" -> AppText.get(R.string.appr_outcome_running)
            "failed" -> AppText.get(R.string.appr_outcome_failed)
            "denied" -> AppText.get(R.string.appr_outcome_denied)
            "expired" -> AppText.get(R.string.appr_outcome_expired)
            else -> status.replaceFirstChar { it.titlecase() }
        }

    /** The failure the executor recorded, when there is one. */
    val error: String?
        get() = (result as? JsonObject)?.get("error")?.let { (it as? JsonPrimitive)?.contentOrNull }

    /** When it was settled: executed, else decided, else created. */
    val settledAt: String? get() = executedAt ?: decidedAt ?: createdAt
}

/** The statuses an approval can have once it is no longer pending. */
const val DECIDED_STATUSES = "approved,executing,executed,failed,denied,expired"

/** Recent decisions, newest first. A server with only the older outbox queue has none. */
suspend fun MailrApi.approvalHistory(limit: Int = 20): List<ApprovalRecord> {
    val el = client.get("approvals", JsonElement.serializer(), mapOf("status" to DECIDED_STATUSES, "limit" to limit))
    val arr = (el as? JsonObject)?.get("items")?.jsonArray ?: el.jsonArray
    return arr.map { MailrJson.decodeFromJsonElement(ApprovalRecord.serializer(), it) }
}

/** A message for the Approvals screen's snackbar; [undoRuleId] offers Undo for a grant (ApprovalsTracker.undoGrant). */
data class ApprovalNotice(val text: String, val undoRuleId: Long? = null)

/**
 * A new approval that arrived while the app was open, for the one-time banner. [more] counts
 * the others that arrived since the banner last showed.
 */
data class Arrival(val approval: Approval, val chatSessionId: String?, val more: Int = 0)

/**
 * Pending approvals for the whole app, with the messages not sent and the reminders showing (the
 * "Waiting for you" screen and its bell in the inbox): the count on the Assistant tab and the bell,
 * the Approvals screen, the one-time banner for a new arrival, and the approval notifications.
 *
 * - The list is read at start, whenever the app comes to the foreground, when the event stream
 *   reconnects (`open`), and on every `approval` or `outbox` event.
 * - An [Arrival] comes only from a `pending` event for an id not seen before. Items found by a
 *   read at start or on resume are known already (they had their system notification), so each
 *   approval is announced at most once per process.
 * - An approval that is no longer pending, wherever it was handled, loses its notification.
 *
 * One tracker per [AppGraph] ([of]); it lives as long as the process.
 */
class ApprovalsTracker(
    private val g: AppGraph,
    events: Flow<ServerEvent> = g.live.events,
    private val scope: CoroutineScope = g.scope,
) {
    private val _pending = MutableStateFlow<List<Approval>?>(null)
    /** Null until the first read. */
    val pending: StateFlow<List<Approval>?> = _pending.asStateFlow()
    private val _history = MutableStateFlow<List<ApprovalRecord>>(emptyList())
    val history: StateFlow<List<ApprovalRecord>> = _history.asStateFlow()
    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()
    private val _refreshing = MutableStateFlow(false)
    val refreshing: StateFlow<Boolean> = _refreshing.asStateFlow()
    private val _arrival = MutableStateFlow<Arrival?>(null)
    val arrival: StateFlow<Arrival?> = _arrival.asStateFlow()
    private val _messages = MutableSharedFlow<ApprovalNotice>(extraBufferCapacity = 4)
    /** "Allowed", "Denied", a grant that decided others too (with Undo), or the error of a decision made on the Approvals screen. */
    val messages: SharedFlow<ApprovalNotice> = _messages
    /** The approval a notification asked to show (its key), until the Approvals screen scrolls to it. */
    val focus = MutableStateFlow<String?>(null)
    private val _notSent = MutableStateFlow<List<OutboxRow>>(emptyList())
    /** Messages that could not be sent (GET /outbox?active=true, status failed): the Not sent section. */
    val notSent: StateFlow<List<OutboxRow>> = _notSent.asStateFlow()
    /** The reminders showing now, fired or missed (this phone's own reminders): the Reminders section. */
    val reminders: StateFlow<List<ReminderEntity>> = g.db.reminders().shownFlow()
        .stateIn(scope, SharingStarted.Eagerly, emptyList())
    /** Open Outbox from the Not sent section: the mail list opens its Outbox sheet and sets this back. */
    val outboxRequest = MutableStateFlow(false)

    /** What the bell counts (WaitingCounts): approvals, messages not sent, reminders and the missed ones. */
    val counts: StateFlow<WaitingCounts> = combine(_pending, _notSent, reminders) { p, n, r ->
        WaitingCounts(p?.size ?: 0, n.size, r.size, r.count { it.missed })
    }.stateIn(scope, SharingStarted.Eagerly, WaitingCounts())

    private val announced = mutableSetOf<String>()
    private val reads = Mutex()

    init {
        scope.launch { refresh(seed = true) }
        scope.launch { events.collect { onEvent(it) } }
        scope.launch { g.foreground.drop(1).filter { it }.collect { refresh(seed = true) } }
        // Signed out: nothing is waiting for this device any more.
        scope.launch {
            g.sessions.session.drop(1).collect { s ->
                if (s == null) {
                    _pending.value = null; _history.value = emptyList(); _arrival.value = null; _notSent.value = emptyList()
                } else refresh(seed = true)
            }
        }
    }

    /**
     * Read the pending list and the history. [seed] marks every pending item as known, so none of
     * them is announced later (the read at start, on resume and after a reconnect).
     */
    suspend fun refresh(seed: Boolean = false, quiet: Boolean = true) = reads.withLock {
        if (!quiet) _refreshing.value = true
        try {
            val list = g.api.approvals()
            if (seed) synchronized(announced) { announced += list.map(NotifyDecisions::approvalKey) }
            _pending.value = list
            _error.value = null
            // Handled anywhere, gone everywhere: a notification whose approval is no longer pending goes.
            Notifier.cancelApprovalsExcept(g.context, list.map(NotifyDecisions::approvalKey).toSet())
        } catch (e: Exception) {
            _error.value = e.userMessage()
        } finally {
            _refreshing.value = false
        }
        _history.value = catching { g.api.approvalHistory() }.getOrDefault(_history.value)
        // An older server, or none reachable: the last list stays.
        _notSent.value = catching { g.api.activeOutbox().filter { it.notSent() } }.getOrDefault(_notSent.value)
    }

    /** ACT draft.retry_send @android.approvals: Retry in the Not sent section. */
    fun retry(row: OutboxRow) {
        scope.launch {
            try {
                g.api.retrySend(row.id)
                _notSent.update { l -> l.filterNot { it.id == row.id } }
                _messages.emit(ApprovalNotice(AppText.get(R.string.appr_sending_again)))
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.emit(ApprovalNotice(e.userMessage()))
            }
            refresh()
        }
    }

    fun refreshNow() {
        scope.launch { refresh(quiet = false) }
    }

    internal suspend fun onEvent(ev: ServerEvent) {
        when (ev.type) {
            "open" -> refresh(seed = true)
            // ACT approval.sync @android.notification: approvals that left pending anywhere (one event for a batch).
            "approval.decided" -> {
                val keys = ev.data["ids"]?.let { it as? kotlinx.serialization.json.JsonArray }
                    ?.mapNotNull { it.jsonPrimitive.longOrNull?.toString() }.orEmpty().toSet()
                if (keys.isNotEmpty()) {
                    io.github.murtazanasir.mailr.data.sync.approvalsDecided(g, keys)
                    _pending.update { list -> list?.filterNot { NotifyDecisions.approvalKey(it) in keys } }
                    _arrival.update { a -> a?.takeIf { NotifyDecisions.approvalKey(it.approval) !in keys } }
                }
            }
            "approval", "outbox" -> {
                val status = ev.data["status"]?.jsonPrimitive?.contentOrNull
                val id = ev.data["id"]?.jsonPrimitive?.longOrNull
                val key = id?.let { if (ev.type == "outbox") "o$it" else "$it" }
                val waiting = status == "pending" || status == "awaiting_approval"
                if (key != null && !waiting) {
                    Notifier.cancelApproval(g.context, key)
                    _pending.update { list -> list?.filterNot { NotifyDecisions.approvalKey(it) == key } }
                    _arrival.update { a -> a?.takeIf { NotifyDecisions.approvalKey(it.approval) != key } }
                }
                val fresh = key != null && waiting && synchronized(announced) { announced.add(key) }
                refresh()
                if (fresh) {
                    val a = _pending.value?.firstOrNull { NotifyDecisions.approvalKey(it) == key }
                        ?: stub(ev, id!!)
                    _arrival.update { prev -> Arrival(a, ev.data["chat_session_id"]?.jsonPrimitive?.contentOrNull,
                        more = if (prev == null) 0 else prev.more + 1) }
                }
            }
        }
    }

    /** The banner was shown long enough, swiped away, or opened. */
    fun dismissArrival() {
        _arrival.value = null
    }

    /** ACT approval.approve @android.approvals, ACT approval.deny @android.approvals: a decision on the Approvals screen. */
    fun decide(a: Approval, approve: Boolean, scope: String) = decide(a, approve, scope, emptyMap())

    /**
     * ACT agent.review_move @android.approvals: Apply on a review card, once, with the user's own moves on it
     * (`changes`, by conversation id), as in the chat (AssistantViewModel.applyReview).
     */
    fun applyReview(a: Approval, changes: Map<Long, String>) = decide(a, true, "once", changes)

    private fun decide(a: Approval, approve: Boolean, scope: String, changes: Map<Long, String>) {
        this.scope.launch {
            try {
                val r = g.api.decide(a, approve, scope, changes = changes)
                val key = NotifyDecisions.approvalKey(a)
                Notifier.cancelApproval(g.context, key)
                _pending.update { list -> list?.filterNot { NotifyDecisions.approvalKey(it) == key } }
                val also = r?.alsoDecided?.count ?: 0
                val rule = r?.ruleId
                _messages.emit(when {
                    // ACT approval.undo_grant @android.approvals: the grant decided other waiting requests too.
                    also > 0 && rule != null && scope != "once" -> ApprovalNotice(
                        if (approve) GrantText.granted(scope, GrantText.sessionLabel(a), also) else GrantText.denied(also), rule)
                    else -> ApprovalNotice(AppText.get(if (approve) R.string.appr_allowed else R.string.appr_denied))
                })
                refresh()
            } catch (e: Exception) {
                _messages.emit(ApprovalNotice(e.userMessage()))
            }
        }
    }

    /** ACT approval.approve_many @android.approvals: "Allow all n" from one chat or agent, each request once. */
    fun decideMany(group: List<Approval>) {
        this.scope.launch {
            try {
                val r = g.api.decideMany(group.map { it.id })
                val keys = group.map(NotifyDecisions::approvalKey).toSet()
                keys.forEach { Notifier.cancelApproval(g.context, it) }
                _pending.update { list -> list?.filterNot { NotifyDecisions.approvalKey(it) in keys } }
                _messages.emit(ApprovalNotice(allowedText(r.count, r.skipped.size)))
                refresh()
            } catch (e: Exception) {
                _messages.emit(ApprovalNotice(e.userMessage()))
            }
        }
    }

    /** Undo of a grant from its snackbar: the rule goes, and the requests it decided that have not run wait again. */
    fun undoGrant(ruleId: Long) {
        scope.launch {
            try {
                val r = g.api.undoGrant(ruleId)
                _messages.emit(ApprovalNotice(GrantText.undone(r.reverted.size, r.kept.size)))
                refresh()
            } catch (e: Exception) {
                _messages.emit(ApprovalNotice(e.userMessage()))
            }
        }
    }

    private fun stub(ev: ServerEvent, id: Long): Approval {
        fun s(k: String) = ev.data[k]?.jsonPrimitive?.contentOrNull.orEmpty()
        return Approval(id, actor = s("actor"), action = s("action"),
            preview = ApprovalPreview(s("title").ifBlank { s("subject") }),
            outboxId = if (ev.type == "outbox") id else null)
    }

    companion object {
        private val all = WeakHashMap<AppGraph, ApprovalsTracker>()

        fun of(g: AppGraph): ApprovalsTracker = synchronized(all) { all.getOrPut(g) { ApprovalsTracker(g) } }
    }
}
