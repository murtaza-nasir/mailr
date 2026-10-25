package io.github.murtazanasir.mailr.ui.conversation

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.data.api.ConversationDetail
import io.github.murtazanasir.mailr.data.api.EventChange
import io.github.murtazanasir.mailr.data.api.Invite
import io.github.murtazanasir.mailr.data.api.Itip
import io.github.murtazanasir.mailr.data.api.Message
import io.github.murtazanasir.mailr.data.api.OutboxRow
import io.github.murtazanasir.mailr.data.api.activeOutbox
import io.github.murtazanasir.mailr.data.api.answerProposal
import io.github.murtazanasir.mailr.data.api.applyEventChange
import io.github.murtazanasir.mailr.data.api.applyUnverified
import io.github.murtazanasir.mailr.data.api.closeEventChange
import io.github.murtazanasir.mailr.data.api.invite
import io.github.murtazanasir.mailr.data.api.inviteReply
import io.github.murtazanasir.mailr.data.api.linkNote
import io.github.murtazanasir.mailr.data.api.noteFromEmail
import io.github.murtazanasir.mailr.data.api.replySuggestions
import io.github.murtazanasir.mailr.data.api.retrySend
import io.github.murtazanasir.mailr.data.api.undoEventChange
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.ui.mail.UiMessage
import io.github.murtazanasir.mailr.ui.util.Format
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats

/*
 * Android reader parity with the web: the invitation card, the
 * suggested replies, Move to, Create from email (Note, Checklist, Link to a note, Meeting) and the
 * send state of a message that did not leave (Retry). The words and rules are here, pure, so the
 * tests read them without a screen; ReaderExtrasUi.kt draws them.
 */

/** The words of the invitation card, as the web's inviteLogic.ts. */
object InviteText {
    private val day: DateTimeFormatter get() = LocaleFormats.date("EEEdMMM", "EEE d MMM")
    private val time: DateTimeFormatter get() = LocaleFormats.date("hmma", "h:mm a")

    private fun at(s: String?, zone: ZoneId): ZonedDateTime? = Format.parse(s)?.withZoneSameInstant(zone)
    private fun isDate(s: String?) = s != null && s.length == 10

    /** The organizer as the card names them: "Name <address>", or the address alone. The address is
     *  always shown, so a name that looks like someone else cannot hide it.
     *  Bidi and invisible characters are gone from both. */
    fun organizerLine(name: String, email: String): String {
        val n = name.filterNot { it in INVISIBLE }.trim()
        val e = email.filterNot { it in INVISIBLE || it.isWhitespace() }
        return if (n.isBlank() || n.equals(e, ignoreCase = true)) e else "$n <$e>"
    }

    private const val INVISIBLE = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200e\u200f\u061c\u200b\u200c\u200d\u2060\ufeff"

    /** "Tue 6 Oct, 3:00 PM – 4:00 PM", "Tue 6 Oct · all day". */
    fun whenText(start: String?, end: String?, zone: ZoneId = ZoneId.systemDefault()): String {
        if (start == null) return ""
        if (isDate(start)) {
            val s = java.time.LocalDate.parse(start)
            val last = end?.takeIf { isDate(it) }?.let { java.time.LocalDate.parse(it).minusDays(1) } ?: s
            return if (last.isAfter(s)) AppText.get(R.string.conv_invite_all_day_range, s.format(day), last.format(day))
            else AppText.get(R.string.conv_invite_all_day, s.format(day))
        }
        val s = at(start, zone) ?: return ""
        val e = at(end, zone)
        val head = AppText.get(R.string.conv_invite_day_time, s.format(day), s.format(time))
        return when {
            e == null -> head
            e.toLocalDate() == s.toLocalDate() -> AppText.get(R.string.conv_invite_range, head, e.format(time))
            else -> AppText.get(R.string.conv_invite_range, head, AppText.get(R.string.conv_invite_day_time, e.format(day), e.format(time)))
        }
    }

    /** "You have Team sync at that time", "You have Team sync and 2 more at that time". */
    fun conflictText(summaries: List<String>): String? {
        if (summaries.isEmpty()) return null
        val first = summaries[0].ifBlank { AppText.get(R.string.conv_invite_another_event) }
        return if (summaries.size > 1) AppText.plural(R.plurals.conv_invite_conflict_more, summaries.size - 1, first, summaries.size - 1)
        else AppText.get(R.string.conv_invite_conflict, first)
    }

    /** The update's "was" line from the ingest record (the web's previousLine). */
    fun previousLine(i: Invite, zone: ZoneId = ZoneId.systemDefault()): String? {
        val it = i.itip ?: return null
        val prev = it.previous?.start ?: return null
        if (it.method != "REQUEST" || !it.timeChanged) return null
        val was = whenText(prev, null, zone)
        return if (it.previous.recurrenceId != null && i.recurring) AppText.get(R.string.conv_invite_occurrence_moved_was, was)
        else AppText.get(R.string.conv_invite_time_changed_was, was)
    }

    fun guestName(i: Invite, email: String?): String {
        if (email.isNullOrBlank()) return AppText.get(R.string.conv_invite_a_guest)
        val a = i.attendees.firstOrNull { it.email.equals(email, ignoreCase = true) }
        return a?.name?.takeIf { it.isNotBlank() } ?: email.substringBefore('@')
    }

    /** "Dana proposes Thu 8 Oct, 3:00 PM" for a COUNTER on an event I organise. */
    fun proposalText(i: Invite, fromEmail: String, zone: ZoneId = ZoneId.systemDefault()): String? {
        val it = i.itip ?: return null
        val start = it.proposed?.start ?: return null
        if (it.method != "COUNTER" || !it.counter) return null
        return AppText.get(R.string.conv_invite_proposes, guestName(i, fromEmail), whenText(start, null, zone))
    }

    /** What the ingest job did, in its words; a guest's answer names the guest. */
    fun outcome(i: Invite): String? {
        val it = i.itip ?: return null
        if (it.detail.isBlank() || (it.method == "COUNTER" && it.counter)) return null
        val base = if (it.method == "REPLY" && it.applied && "—" !in it.detail)
            AppText.get(R.string.conv_invite_guest_answered, guestName(i, it.attendee), it.partstat.orEmpty().lowercase()) else it.detail
        return base + (it.comment?.takeIf { c -> c.isNotBlank() }?.let { c -> AppText.get(R.string.conv_invite_comment_suffix, c) } ?: "")
    }

    /** The answer line before the buttons. */
    fun statusLabel(myStatus: String?): String = when (myStatus?.uppercase()) {
        "ACCEPTED" -> AppText.get(R.string.conv_invite_said_yes)
        "TENTATIVE" -> AppText.get(R.string.conv_invite_said_maybe)
        "DECLINED" -> AppText.get(R.string.conv_invite_said_no)
        else -> AppText.get(R.string.conv_invite_going)
    }

    /** A request I can answer (not cancelled, an invitation, I am a guest). */
    fun answerable(i: Invite): Boolean = !i.cancelled && i.method in setOf("REQUEST", "", "PUBLISH") && i.myStatus != null

    /** The toast after an answer. */
    fun answered(response: String, calendarFailed: Boolean): String =
        AppText.get(when (response) {
            "accepted" -> R.string.conv_invite_answered_yes
            "declined" -> R.string.conv_invite_answered_no
            else -> R.string.conv_invite_answered_maybe
        }) + if (calendarFailed) AppText.get(R.string.conv_invite_calendar_failed_suffix) else ""
}

/** An attachment that makes a message an invitation (the web's hasInvite). */
fun Message.hasInvite(): Boolean = attachments.any { a ->
    a.type.substringBefore(';').trim().lowercase() in setOf("text/calendar", "application/ics") || a.filename.endsWith(".ics", ignoreCase = true) ||
        a.email?.invite == true
}

/** Where Move to can send a conversation (the web's MoveChoices): the places, then the user's labels. */
data class MoveTarget(val label: String, val action: String, val labelId: Long? = null)

object MoveTo {
    /** [place] is the reader's place: Spam and Trash move back to the Inbox; elsewhere Archive, Spam and Trash. */
    fun targets(labels: List<io.github.murtazanasir.mailr.data.api.Label>, place: io.github.murtazanasir.mailr.ui.mail.MailPlace, filter: String = ""): List<MoveTarget> {
        val fixed = when (place) {
            io.github.murtazanasir.mailr.ui.mail.MailPlace.TRASH -> listOf(MoveTarget(AppText.get(R.string.conv_move_inbox), "untrash"))
            io.github.murtazanasir.mailr.ui.mail.MailPlace.SPAM -> listOf(MoveTarget(AppText.get(R.string.conv_move_inbox), "not_spam"), MoveTarget(AppText.get(R.string.conv_move_trash), "trash"))
            else -> listOf(MoveTarget(AppText.get(R.string.conv_move_archive), "archive"), MoveTarget(AppText.get(R.string.conv_move_spam), "spam"), MoveTarget(AppText.get(R.string.conv_move_trash), "trash"))
        }
        val q = filter.trim().lowercase()
        val user = labels.filter { it.role == null && (q.isEmpty() || q in it.name.lowercase()) }
            .sortedBy { it.name.lowercase() }.map { MoveTarget(it.name, "move", it.id) }
        return (if (q.isEmpty()) fixed else fixed.filter { q in it.label.lowercase() }) + user
    }
}

/** A meeting's guests: everyone in the thread except me (the web's participants()). */
fun meetingGuests(d: ConversationDetail, mine: Set<String>): List<Address> {
    val seen = LinkedHashMap<String, Address>()
    d.messages.filter { !it.draft }.forEach { m ->
        (listOf(m.from) + m.to + m.cc).forEach { a ->
            val e = a.email.lowercase()
            if (e.isNotBlank() && e !in mine && !e.startsWith("noreply") && !e.startsWith("no-reply") && e !in seen) seen[e] = a
        }
    }
    return seen.values.toList()
}

/** A message on its way or not sent (the web's sendStateFor, for a saved draft). */
fun sendStateOf(m: Message, rows: List<OutboxRow>): OutboxRow? =
    if (!m.draft) null else rows.firstOrNull { it.draftId == m.id && it.status != "sent" }

/** A send error in plain words (the web's plainSendError). */
fun plainSendError(error: String): String {
    val e = error.lowercase()
    return when {
        e.isBlank() -> AppText.get(R.string.conv_send_error_default)
        "interrupted" in e -> AppText.get(R.string.conv_send_error_interrupted)
        Regex("recipient|5\\.1\\.[0-9]|user unknown|no such user|mailbox unavailable|does not exist").containsMatchIn(e) -> AppText.get(R.string.conv_send_error_recipient)
        Regex("auth|535|password|credential|login|sign.?in").containsMatchIn(e) -> AppText.get(R.string.conv_send_error_auth)
        Regex("timed? ?out|timeout|connect|unreachable|network|resolve|dns").containsMatchIn(e) -> AppText.get(R.string.conv_send_error_network)
        Regex("too large|size|552").containsMatchIn(e) -> AppText.get(R.string.conv_send_error_size)
        Regex("spam|block|policy|reject").containsMatchIn(e) -> AppText.get(R.string.conv_send_error_rejected)
        else -> AppText.get(R.string.conv_send_error_default)
    }
}

/**
 * The reader's extra state, owned by ConversationViewModel: the invitations of its messages, the
 * suggested replies for the newest message from someone else, and the outbox rows of its drafts.
 */
class ReaderExtras(
    private val g: AppGraph,
    private val conversationId: Long,
    private val scope: CoroutineScope,
    private val say: suspend (UiMessage) -> Unit,
) {
    private val _invites = MutableStateFlow<Map<Long, Invite>>(emptyMap())
    val invites: StateFlow<Map<Long, Invite>> = _invites.asStateFlow()
    private val _busy = MutableStateFlow<Set<Long>>(emptySet())
    val busy: StateFlow<Set<Long>> = _busy.asStateFlow()
    private val _replies = MutableStateFlow<Pair<Long, List<String>>?>(null)
    /** (message id, short replies) for the newest message from someone else. */
    val replies: StateFlow<Pair<Long, List<String>>?> = _replies.asStateFlow()
    private val _outbox = MutableStateFlow<List<OutboxRow>>(emptyList())
    val outbox: StateFlow<List<OutboxRow>> = _outbox.asStateFlow()
    private var asked = emptySet<Long>()
    private var askedReplies = emptySet<Long>()
    private val _translations = MutableStateFlow<Map<Long, Translation>>(emptyMap())
    /** Each message's translation (mail.translate), by message id. */
    val translations: StateFlow<Map<Long, Translation>> = _translations.asStateFlow()

    /** ACT mail.translate @android.reader: the message's text in [to]; the server's refusal (local-only, no model) as it comes. */
    fun translate(messageId: Long, to: String) {
        if (_translations.value[messageId] is Translation.Working) return
        _translations.update { it + (messageId to Translation.Working(to)) }
        scope.launch {
            val t = try {
                val r = g.api.translate(messageId, to)
                when (r.status) {
                    "ready" -> Translation.Ready(r)
                    "empty" -> Translation.Refused(to, AppText.get(R.string.conv_translate_no_text))
                    else -> Translation.Refused(to, r.reason?.takeIf { it.isNotBlank() } ?: AppText.get(R.string.conv_translate_no_model))
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Translation.Failed(to, e.userMessage())
            }
            _translations.update { it + (messageId to t) }
        }
    }

    private val _attached = MutableStateFlow<Map<String, AttachedState>>(emptyMap())
    /** Each opened attached email (mail.open_attached_email), by [attachedKey]. */
    val attached: StateFlow<Map<String, AttachedState>> = _attached.asStateFlow()

    /**
     * ACT mail.open_attached_email @android.reader: load the attached email at [part] of [messageId].
     * [allowImages] null: the phone's image choice (spam never loads them on its own); true: the
     * reader asked to show them.
     */
    fun loadAttached(messageId: Long, part: Int, allowImages: Boolean? = null) {
        val key = attachedKey(messageId, part)
        val current = _attached.value[key]
        if (allowImages == null && (current is AttachedState.Ready || current == AttachedState.Loading)) return
        if (current !is AttachedState.Ready) _attached.update { it + (key to AttachedState.Loading) }
        scope.launch {
            val r = try {
                val images = allowImages ?: g.settings.prefs.first().showImages()
                val b = g.api.attachedEmail(messageId, part, images)
                if (allowImages == null && images && b.junk) AttachedState.Ready(g.api.attachedEmail(messageId, part, false), false)
                else AttachedState.Ready(b, images)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                AttachedState.Failed(e.userMessage())
            }
            _attached.update { it + (key to r) }
        }
    }

    /** Show original (false) or the translation again (true). */
    fun showTranslation(messageId: Long, on: Boolean) {
        _translations.update { m -> (m[messageId] as? Translation.Ready)?.let { m + (messageId to it.copy(showing = on)) } ?: m }
    }

    /** Load what the conversation needs, once per message. */
    fun onDetail(d: ConversationDetail, suggestionsOn: Boolean) {
        d.messages.filter { it.hasInvite() && !it.sentByMe && it.id !in asked }.forEach { m ->
            asked = asked + m.id
            scope.launch { loadInvite(m.id) }
        }
        val last = d.messages.lastOrNull { !it.draft }
        if (suggestionsOn && last != null && !last.sentByMe && last.id !in askedReplies) {
            askedReplies = askedReplies + last.id
            scope.launch {
                catching { g.api.replySuggestions(last.id) }.onSuccess { r ->
                    if (r.status == "ready" && r.replies.isNotEmpty()) _replies.value = last.id to r.replies.take(3)
                }.onFailure { if (it is CancellationException) throw it }
            }
        }
        if (d.messages.any { it.draft }) scope.launch { loadOutbox() }
    }

    private suspend fun loadInvite(id: Long) {
        try {
            val i = g.api.invite(id)
            _invites.update { it + (id to i) }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            // Not an invitation the server can read: no card, as on the web.
        }
    }

    suspend fun loadOutbox() {
        try {
            _outbox.value = g.api.activeOutbox().filter { it.conversationId == conversationId }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
        }
    }

    private fun run(id: Long, block: suspend () -> Unit) {
        if (id in _busy.value) return
        _busy.update { it + id }
        scope.launch {
            try {
                block()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                say(UiMessage(e.userMessage()))
            } finally {
                _busy.update { it - id }
                loadInvite(id)
                catching { g.calendar.sync() }
            }
        }
    }

    /** ACT event.rsvp @android.reader: Yes, Maybe or No; `send` false adds it as Maybe without telling the organiser. */
    fun answer(messageId: Long, response: String, comment: String = "", send: Boolean = true) = run(messageId) {
        val r = g.api.inviteReply(messageId, response, comment, send)
        val failed = ((r["calendar"] as? JsonPrimitive)?.takeIf { it.isString }?.content ?: "").startsWith("error")
        say(UiMessage(if (send) InviteText.answered(response, failed) else AppText.get(R.string.conv_invite_added_maybe_silent)))
    }

    // ACT invite.apply_unverified @android.reader
    fun applyAnyway(messageId: Long) = run(messageId) {
        val r: Itip = g.api.applyUnverified(messageId)
        say(UiMessage(if (r.applied) AppText.get(R.string.conv_invite_calendar_updated) else r.detail.ifBlank { AppText.get(R.string.conv_invite_nothing_changed) }))
    }

    /** ACT event.accept_proposal @android.reader, ACT event.decline_proposal @android.reader, ACT invite.counter @android.reader */
    fun proposal(messageId: Long, invite: Invite, accept: Boolean, guest: String) = run(messageId) {
        g.api.answerProposal(invite.uid, messageId, accept)
        say(UiMessage(if (accept) AppText.get(R.string.conv_proposal_accepted) else AppText.get(R.string.conv_proposal_declined, guest)))
    }

    /** ACT invite.apply_cancel @android.reader, ACT invite.remove_cancelled @android.reader: "Remove from calendar"
     *  after the organiser cancelled; never silently, Undo puts the event back. */
    fun removeCancelled(messageId: Long, invite: Invite) = run(messageId) {
        g.api.client.send("POST", "messages/$messageId/itip/remove", JsonObject.serializer())
        say(UiMessage(AppText.get(R.string.conv_invite_removed), AppText.get(R.string.conv_undo)) {
            g.api.client.send("POST", "messages/$messageId/itip/restore", JsonObject.serializer())
            loadInvite(messageId)
            catching { g.calendar.sync() }
        })
    }

    /** ACT invite.link_existing @android.reader: my copy under another UID takes the invitation's identity; Undo. */
    fun linkInvite(messageId: Long, uid: String) = run(messageId) {
        g.api.client.send("POST", "messages/$messageId/invite/link", JsonObject.serializer(),
            io.github.murtazanasir.mailr.data.api.MailrJson.encodeToString(JsonObject.serializer(), JsonObject(mapOf("uid" to JsonPrimitive(uid)))))
        say(UiMessage(AppText.get(R.string.conv_invite_linked), AppText.get(R.string.conv_undo)) {
            g.api.client.send("POST", "messages/$messageId/invite/unlink", JsonObject.serializer())
            loadInvite(messageId)
            catching { g.calendar.sync() }
        })
    }

    private val _changesDone = MutableStateFlow<Set<Long>>(emptySet())
    /** Messages whose suggested move was taken or put aside here: the card goes away at once (the web's `gone`). */
    val changesDone: StateFlow<Set<Long>> = _changesDone.asStateFlow()
    private val _changesBusy = MutableStateFlow<Set<Long>>(emptySet())
    val changesBusy: StateFlow<Set<Long>> = _changesBusy.asStateFlow()

    /**
     * ACT event.change_suggestion @android.reader: Move. The event takes the new time; the snackbar
     * says so, with Undo, which puts the old time back (the card stays closed, as on the web).
     */
    fun moveSuggested(messageId: Long, c: EventChange) = changeRun(messageId, AppText.get(R.string.conv_change_not_moved)) {
        g.api.applyEventChange(messageId, c)
        _changesDone.update { it + messageId }
        scope.launch { catching { g.calendar.sync() }.onFailure { if (it is CancellationException) throw it } }
        say(UiMessage(AppText.get(R.string.conv_change_moved, c.summary), AppText.get(R.string.conv_undo)) {
            try {
                g.api.undoEventChange(c)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                say(UiMessage(AppText.get(R.string.conv_change_not_changed) + ": " + e.userMessage()))
            }
            catching { g.calendar.sync() }.onFailure { if (it is CancellationException) throw it }
        })
    }

    /** ACT event.change_suggestion @android.reader: Not now. The suggestion closes; nothing changes in the calendar. */
    fun dismissSuggested(messageId: Long) = changeRun(messageId, null) {
        g.api.closeEventChange(messageId, "dismissed")
        _changesDone.update { it + messageId }
    }

    private fun changeRun(id: Long, failed: String?, block: suspend () -> Unit) {
        if (id in _changesBusy.value || id in _changesDone.value) return
        _changesBusy.update { it + id }
        scope.launch {
            try {
                block()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                say(UiMessage(failed?.let { "$it: ${e.userMessage()}" } ?: e.userMessage()))
            } finally {
                _changesBusy.update { it - id }
            }
        }
    }

    // ACT draft.retry_send @android.reader
    fun retry(row: OutboxRow) {
        scope.launch {
            try {
                g.api.retrySend(row.id)
                say(UiMessage(AppText.get(R.string.conv_sending_again)))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                say(UiMessage(e.userMessage()))
            }
            loadOutbox()
        }
    }

    /** ACT note.from_email @android.reader: a note or a checklist from this conversation, linked to it. */
    fun note(kind: String, open: (String) -> Unit) {
        scope.launch {
            try {
                val made = g.api.noteFromEmail(conversationId, kind).note
                val id = made?.id.orEmpty()
                say(if (kind == "checklist") UiMessage(AppText.get(R.string.conv_checklist_made), AppText.get(R.string.conv_open)) { open(id) }
                    else UiMessage(AppText.get(R.string.conv_note_made), AppText.get(R.string.conv_open)) { open(id) })
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                say(UiMessage(e.userMessage()))
            }
        }
    }

    // ACT note.link @android.reader
    fun link(noteId: String, title: String) {
        scope.launch {
            try {
                g.api.linkNote(noteId, conversationId)
                say(UiMessage(AppText.get(R.string.conv_linked_to, title.ifBlank { AppText.get(R.string.conv_the_note) })))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                say(UiMessage(e.userMessage()))
            }
        }
    }
}
