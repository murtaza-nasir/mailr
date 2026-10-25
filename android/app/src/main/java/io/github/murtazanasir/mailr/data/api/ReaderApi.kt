package io.github.murtazanasir.mailr.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/*
 * The reader's parity calls: invitations, suggested replies, templates, notes from an
 * email, the Outbox; and the phone's setup facts (GET /me setup, the push server, invites).
 * Each is a thin call to the route the web uses.
 */

/** GET /messages/{mid}/invite: an invitation as InviteCard shows it (backend calendar/routes.py get_invite). */
@Serializable
data class Invite(
    val method: String = "",
    val uid: String = "",
    @Serializable(with = SafeTextSerializer::class) val summary: String = "",
    val start: String? = null,
    val end: String? = null,
    @Serializable(with = SafeTextSerializer::class) val location: String = "",
    val organizer: InviteOrganizer = InviteOrganizer(),
    val attendees: List<InviteAttendee> = emptyList(),
    @SerialName("my_status") val myStatus: String? = null,
    val recurring: Boolean = false,
    val cancelled: Boolean = false,
    @SerialName("in_calendar") val inCalendar: Boolean = false,
    @Serializable(with = io.github.murtazanasir.mailr.data.cal.IanaZoneSerializer::class) val tz: String? = null,
    val itip: Itip? = null,
    /** Busy events of mine that overlap the slot (the server's check). */
    val conflicts: List<InviteConflict> = emptyList(),
    /** This invitation's copy in my calendar (backend calendar/from_mail.py): by uid, link or time_title. */
    @SerialName("calendar_match") val calendarMatch: InviteMatch? = null,
)

@Serializable
data class InviteMatch(val uid: String = "", val by: String = "uid")

@Serializable
data class InviteOrganizer(@Serializable(with = SafeAddressSerializer::class) val email: String = "", @Serializable(with = SafeTextSerializer::class) val name: String = "")

@Serializable
data class InviteAttendee(@Serializable(with = SafeAddressSerializer::class) val email: String = "", @Serializable(with = SafeTextSerializer::class) val name: String = "", val status: String = "")

@Serializable
data class InviteConflict(@Serializable(with = SafeTextSerializer::class) val summary: String = "", val start: String? = null, val end: String? = null)

/** What the inbound iTIP job did with the message (calendar/inbound.py). */
@Serializable
data class Itip(
    val applied: Boolean = false,
    val detail: String = "",
    val method: String = "",
    val attendee: String? = null,
    val partstat: String? = null,
    @Serializable(with = SafeTextSerializer::class) val comment: String? = null,
    @SerialName("time_changed") val timeChanged: Boolean = false,
    val previous: ItipWhen? = null,
    val pending: Boolean = false,
    val counter: Boolean = false,
    val proposed: ItipWhen? = null,
)

@Serializable
data class ItipWhen(val start: String? = null, val end: String? = null, @SerialName("recurrence_id") val recurrenceId: String? = null)

@Serializable
data class ReplySuggestions(val status: String = "none", val replies: List<String> = emptyList())

@Serializable
data class MailTemplate(val id: Long, @Serializable(with = SafeTextSerializer::class) val name: String = "", val shortcut: String = "",
                        val subject: String = "", @SerialName("body_html") val bodyHtml: String = "")

@Serializable
data class TemplateList(val items: List<MailTemplate> = emptyList())

@Serializable
data class ExpandedTemplate(val id: Long = 0, val subject: String = "", @SerialName("body_html") val bodyHtml: String = "")

/** The Outbox's rows (GET /outbox?active=true): failed sends carry their error. */
@Serializable
data class OutboxRow(
    val id: Long,
    val status: String,
    val subject: String = "",
    val recipients: List<String> = emptyList(),
    val error: String = "",
    @SerialName("send_at") val sendAt: String? = null,
    @SerialName("conversation_id") val conversationId: Long? = null,
    @SerialName("draft_id") val draftId: Long? = null,
)

/** GET /me `setup`: whether the account is set up; `url` is where to finish on the web. */
@Serializable
data class SetupState(
    @SerialName("has_accounts") val hasAccounts: Boolean = true,
    val guide: String = "none",
    val calendar: Boolean = true,
    val url: String = "",
)

/** The instance's own push server (GET /push/targets `server`). */
@Serializable
data class PushServer(val url: String = "", val host: String = "", val secure: Boolean = true, val app: String = "ntfy")

@Serializable
data class PushServerAnswer(val server: PushServer? = null)

/** POST /invites/{token}/accept (household.join): where the setup goes next. */
@Serializable
data class InviteAccepted(val next: String = "/", val applied: Boolean = false)

@Serializable
data class NoteMade(val note: NoteRef? = null)

@Serializable
data class NoteRef(val id: String = "", @Serializable(with = SafeTextSerializer::class) val title: String = "", val kind: String = "text")

@Serializable
data class NoteSearchAnswer(val notes: List<NoteRef> = emptyList())

// ACT invite.view @android.reader
suspend fun MailrApi.invite(messageId: Long): Invite = client.get("messages/$messageId/invite", Invite.serializer())

/**
 * ACT event.rsvp @android.reader: Yes, Maybe or No from the card (POST /calendar/invite-reply); `send` false adds
 * it as Maybe without telling the organiser.
 */
suspend fun MailrApi.inviteReply(messageId: Long, response: String, comment: String = "", send: Boolean = true): JsonObject =
    client.send("POST", "calendar/invite-reply", JsonObject.serializer(), MailrJson.encodeToString(buildJsonObject {
        put("message_id", messageId); put("response", response); put("comment", comment); put("send", send)
    }))

/**
 * ACT event.change_suggestion @android.reader: Move on a suggested move. The event takes the new time
 * (PATCH /calendar/events/{uid}, the user's own change), then the suggestion closes as applied.
 */
suspend fun MailrApi.applyEventChange(messageId: Long, c: EventChange) {
    patchEventTime(c.uid, c.newStart, c.newEnd)
    closeEventChange(messageId, "applied")
}

/** Undo after Move: the event goes back to its old time. */
suspend fun MailrApi.undoEventChange(c: EventChange) = patchEventTime(c.uid, c.oldStart, c.oldEnd)

/** ACT event.change_suggestion @android.reader: Not now (status "dismissed") or after Move ("applied"). */
suspend fun MailrApi.closeEventChange(messageId: Long, status: String): JsonObject =
    client.send("POST", "messages/$messageId/event-change", JsonObject.serializer(),
        MailrJson.encodeToString(buildJsonObject { put("status", status) }))

private suspend fun MailrApi.patchEventTime(uid: String, start: String, end: String?): JsonObject =
    // ApiClient encodes path segments itself (encoding here too sent "%2540" for an "@").
    client.send("PATCH", "calendar/events/$uid", JsonObject.serializer(),
        MailrJson.encodeToString(buildJsonObject { put("start", start); end?.let { put("end", it) } }))

// ACT invite.apply_unverified @android.reader
suspend fun MailrApi.applyUnverified(messageId: Long): Itip =
    client.send("POST", "messages/$messageId/itip/apply", Itip.serializer())

// ACT event.accept_proposal @android.reader, ACT event.decline_proposal @android.reader
suspend fun MailrApi.answerProposal(uid: String, messageId: Long, accept: Boolean): JsonObject =
    client.send("POST", "calendar/events/$uid/counter/${if (accept) "accept" else "decline"}",
        JsonObject.serializer(), MailrJson.encodeToString(buildJsonObject { put("message_id", messageId) }))

// ACT mail.reply_suggestions @android.reader
suspend fun MailrApi.replySuggestions(messageId: Long): ReplySuggestions =
    client.get("messages/$messageId/reply-suggestions", ReplySuggestions.serializer())

// ACT template.insert @android.editor
suspend fun MailrApi.templates(): List<MailTemplate> = client.get("templates", TemplateList.serializer()).items

suspend fun MailrApi.expandTemplate(id: Long, to: List<Address>, identityId: Long?): ExpandedTemplate =
    client.send("POST", "templates/$id/expand", ExpandedTemplate.serializer(), MailrJson.encodeToString(buildJsonObject {
        put("to", JsonArray(to.map { a -> buildJsonObject { put("email", a.email); put("name", a.name.orEmpty()) } }))
        identityId?.let { put("identity_id", it) }
    }), idempotencyKey = null)

// ACT note.from_email @android.reader: a note or a checklist made from the conversation, linked to it.
suspend fun MailrApi.noteFromEmail(conversationId: Long, kind: String): NoteMade =
    client.send("POST", "notes/from-email", NoteMade.serializer(), MailrJson.encodeToString(buildJsonObject {
        put("conversation_id", conversationId); put("kind", kind)
    }))

/** Notes to link the conversation to: the search box of the link sheet (empty: recent notes). */
suspend fun MailrApi.findNotes(q: String): List<NoteRef> =
    if (q.isBlank()) client.get("notes", NoteSearchAnswer.serializer(), mapOf("view" to "all")).notes.take(30)
    else client.get("notes/search", NoteSearchAnswer.serializer(), mapOf("q" to q, "limit" to 20)).notes

// ACT note.link @android.reader: the conversation as a link on the note.
suspend fun MailrApi.linkNote(noteId: String, conversationId: Long): JsonObject =
    client.send("POST", "notes/$noteId/links", JsonObject.serializer(), MailrJson.encodeToString(buildJsonObject {
        put("target_kind", "conversation")
        put("target_ref", buildJsonObject { put("conversation_id", conversationId) })
    }))

/** `/note` in the chat: a text note (POST /notes), made at the server at once so links can follow. */
suspend fun MailrApi.createTextNote(title: String, body: String): NoteMade =
    client.send("POST", "notes", NoteMade.serializer(), MailrJson.encodeToString(buildJsonObject {
        put("kind", "text"); put("title", title); put("body", body)
    }))

/** A link on a note (POST /notes/{id}/links): `target_kind` and `target_ref`, as the web sends them. */
suspend fun MailrApi.addNoteLink(noteId: String, link: JsonObject): JsonObject =
    client.send("POST", "notes/$noteId/links", JsonObject.serializer(), MailrJson.encodeToString(link))

// ACT draft.outbox @android.drawer: what has not left yet, or did not leave.
suspend fun MailrApi.activeOutbox(): List<OutboxRow> =
    client.get("outbox", ListSerializer(OutboxRow.serializer()), mapOf("active" to "true"))

// ACT draft.retry_send @android.reader @android.drawer
suspend fun MailrApi.retrySend(id: Long): JsonObject = client.send("POST", "outbox/$id/retry", JsonObject.serializer())

suspend fun MailrApi.pushServer(): PushServer? = client.get("push/targets", PushServerAnswer.serializer()).server

// ACT household.join @android.setup
suspend fun MailrApi.acceptInvite(token: String): InviteAccepted =
    client.send("POST", "invites/$token/accept", InviteAccepted.serializer(), "{}", idempotencyKey = null)

/** Did not leave, or may not have: "uncertain" is a send whose connection dropped after the message was handed
 * over, which the user decides about (Retry), as the web's notSent. */
fun OutboxRow.notSent(): Boolean = status == "failed" || status == "uncertain"

/** The rows the Outbox lists, and whose number the drawer shows: not left yet, or not sent (as the web's inOutbox). */
fun OutboxRow.inOutbox(): Boolean = status == "queued" || status == "sending" || notSent()

/** Keep a value from JSON that may be absent. */
internal fun JsonObject.str(key: String): String? = (this[key] as? JsonPrimitive)?.takeIf { it.isString }?.content
