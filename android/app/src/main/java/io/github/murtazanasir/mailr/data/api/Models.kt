package io.github.murtazanasir.mailr.data.api

import io.github.murtazanasir.mailr.ui.present.localized
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

// Mirrors the REST API and frontend/src/api/types.ts. Unknown fields are ignored, so the app
// keeps working when the server adds fields.

@Serializable
data class Address(@Serializable(with = SafeAddressSerializer::class) val email: String, @Serializable(with = SafeTextSerializer::class) val name: String? = null) {
    val display: String get() = name?.takeIf { it.isNotBlank() } ?: email
}

@Serializable
data class Identity(
    val id: Long,
    val email: String,
    val name: String = "",
    @SerialName("reply_to") val replyTo: String = "",
    @SerialName("signature_html") val signatureHtml: String = "",
    @SerialName("is_default") val isDefault: Boolean = false,
    /** The address has its own picture (GET identities/{id}/avatar); else the account picture shows. */
    @SerialName("has_avatar") val hasAvatar: Boolean = false,
)

/** The account picture (GET me/picture), GET /me `picture`. */
@Serializable
data class AccountPicture(
    val has: Boolean = false,
    /** "upload" or "idp" (from the sign-in provider). */
    val source: String? = null,
    /** Changes with the picture, for cache-busting. */
    val version: Long = 0,
    /** The sign-in provider sent a picture at the last sign-in ("Use my sign-in picture"). */
    @SerialName("idp_available") val idpAvailable: Boolean = false,
)

@Serializable
data class Me(
    val user: User,
    val csrf: String? = null,
    val settings: JsonObject = JsonObject(emptyMap()),
    val identities: List<Identity> = emptyList(),
    val counts: Map<String, Int> = emptyMap(),
    val features: Features = Features(),
    val picture: AccountPicture? = null,
    /** Which model features the account's access policies allow (GET /me `access`); an older server omits it. */
    val access: Access = Access(),
    /** Whether the account is set up (GET /me `setup`); null from an older server. */
    val setup: SetupState? = null,
    /** The server's version (GET /me `version`), shown at the foot of Settings; null from an older server. */
    val version: String? = null,
)

/**
 * GET /me `access`: the model features the user's identity-provider groups allow. Admins are
 * unrestricted. A missing field (an older server) reads as unrestricted, so nothing turns off.
 */
@Serializable
data class Access(
    val unrestricted: Boolean = true,
    val groups: List<String> = emptyList(),
    /** "idp" or "assigned". */
    val source: String? = null,
    val policies: List<String> = emptyList(),
    val features: AccessFeatures = AccessFeatures(),
    val tasks: Map<String, TaskAccess> = emptyMap(),
    @SerialName("needs_endpoints") val needsEndpoints: Boolean = false,
    /** Why the assistant is off, in the server's words ("Your administrator hasn't given you access…"). */
    val message: String? = null,
)

@Serializable
data class AccessFeatures(
    val assistant: Boolean = true,
    @SerialName("choose_models") val chooseModels: Boolean = true,
    val lab: Boolean = true,
    @SerialName("api_tokens") val apiTokens: Boolean = true,
    @SerialName("hosted_models") val hostedModels: Boolean = true,
)

/** One task's allowed models (`access.tasks.chat`, `.classify`). [all]: any model. */
@Serializable
data class TaskAccess(
    val all: Boolean = true,
    val allowed: List<AllowedModels> = emptyList(),
    val default: ModelRef? = null,
)

/** [models] null: every model on the endpoint. */
@Serializable
data class AllowedModels(val endpoint: String, val locality: String = "", val models: List<String>? = null)

@Serializable
data class ModelRef(val endpoint: String? = null, val model: String? = null)

/**
 * Whether the assistant can be asked (the Ask capture, the Assistant tab). Null: it can. Otherwise
 * the message to show instead of a chat. [cached] is the last message the app saw, for a capture
 * opened before the app has asked the server this run.
 */
object AssistantGate {
    /** Ambrose's name until GET /assistant/profile says otherwise ([io.github.murtazanasir.mailr.i18n.AssistantName]). */
    const val DEFAULT_NAME = io.github.murtazanasir.mailr.i18n.AssistantName.DEFAULT
    val DEFAULT_MESSAGE: String get() = AppText.get(R.string.api_assistant_no_access, io.github.murtazanasir.mailr.i18n.AssistantName.now())

    fun blocked(me: Me?, cached: String? = null): String? {
        if (me == null) return cached?.takeIf { it.isNotBlank() }
        val a = me.access
        if (a.unrestricted || a.features.assistant) return null
        return a.message?.takeIf { it.isNotBlank() } ?: DEFAULT_MESSAGE
    }

    /** A chat error that means the assistant is off for this user: SSE `code: no_access` or HTTP 403. */
    fun isNoAccess(code: String?, status: Int?): Boolean = code == "no_access" || status == 403
}

/** What the server offers beyond the basics (GET /me `features`). */
@Serializable
data class Features(
    /** POST /assistant/transcribe: dictation through the user's own speech server. */
    val transcribe: Boolean = false,
)

@Serializable
data class Transcription(
    val text: String = "",
    /** Why the server returned no text ("no speech": silence or a stock phrase Whisper invents). */
    val dropped: String? = null,
)

@Serializable
data class Label(
    val id: Long,
    val name: String,
    val role: String? = null,
    val color: String? = null,
    @SerialName("show_in_list") val showInList: String = "show",
    /** The mail account the label was made in (a folder of that account); null for other labels. */
    @SerialName("home_account_id") val homeAccountId: Long? = null,
    val unread: Int = 0,
    val total: Int = 0,
) {
    val leaf: String get() = name.substringAfterLast('/')
    val depth: Int get() = name.count { it == '/' }
}

@Serializable
data class Participant(@Serializable(with = SafeAddressSerializer::class) val email: String, @Serializable(with = SafeTextSerializer::class) val name: String? = null, val me: Boolean = false)

@Serializable
data class AttachmentPreview(@Serializable(with = SafeTextSerializer::class) val filename: String, val type: String = "")

@Serializable
data class ConversationSummary(
    val id: Long,
    @Serializable(with = SafeTextSerializer::class) val subject: String = "",
    @Serializable(with = SafeSnippetSerializer::class) val snippet: String = "",
    @SerialName("last_date") val lastDate: String? = null,
    @SerialName("message_count") val messageCount: Int = 1,
    @SerialName("unread_count") val unreadCount: Int = 0,
    @SerialName("draft_count") val draftCount: Int = 0,
    @SerialName("has_attachments") val hasAttachments: Boolean = false,
    val starred: Boolean = false,
    val important: Boolean = false,
    val category: String? = null,
    @SerialName("label_ids") val labelIds: List<Long> = emptyList(),
    val participants: List<Participant> = emptyList(),
    /** A note to self: the user wrote every message, each only to their own addresses (backend core/threading.py). */
    @SerialName("to_self") val toSelf: Boolean = false,
    /** When only the user wrote in it: whom it went to, for "To: Dana" in the row. */
    val recipients: List<Participant> = emptyList(),
    @SerialName("snoozed_until") val snoozedUntil: String? = null,
    val muted: Boolean = false,
    /** mail.follow_up: when to check for a reply; nudgedAt: it came back with none. */
    @SerialName("follow_up_at") val followUpAt: String? = null,
    @SerialName("nudged_at") val nudgedAt: String? = null,
    @SerialName("attachments_preview") val attachmentsPreview: List<AttachmentPreview> = emptyList(),
    @SerialName("primary_action") val primaryAction: PrimaryAction? = null,
    /** Triage signals on the newest message: needs_reply, waiting_on, deadline (with its date). */
    val signals: List<Signal> = emptyList(),
    /** The mail accounts it belongs to (ids, in account order), for the row's dots; only with two or more. */
    val accounts: List<Long> = emptyList(),
    /** A search matched it only inside an attached email (the row says so). */
    @SerialName("match_in_attached") val matchInAttached: Boolean = false,
) {
    val unread: Boolean get() = unreadCount > 0
}

/** A mail account as the mailbox shows it (GET /counts `accounts`, backend core/mailaccounts.py). */
@Serializable
data class MailAccountInfo(
    val id: Long,
    @Serializable(with = SafeTextSerializer::class) val address: String = "",
    @Serializable(with = SafeTextSerializer::class) val name: String = "",
    val color: String = "blue",
    @SerialName("identity_id") val identityId: Long? = null,
)

@Serializable
data class ViewCount(val unread: Int = 0, val total: Int = 0)

/** GET /counts, the parts the app reads: the accounts and, with their folders shown, `acct:<id>:<view>` numbers. */
@Serializable
data class SidebarCounts(val views: Map<String, ViewCount> = emptyMap(), val accounts: List<MailAccountInfo> = emptyList())

@Serializable
data class Signal(val name: String = "", val reason: String? = null, val date: String? = null)

/** The main action an email offers (Track package, Pay, Join…). */
@Serializable
/** mail.primary_action; kind "code" carries a one-time `code` to copy instead of a link. */
data class PrimaryAction(val kind: String = "", val label: String = "", val url: String = "", val fact: String? = null,
                         val code: String? = null)

@Serializable
data class Page<T>(
    val items: List<T> = emptyList(),
    @SerialName("next_cursor") val nextCursor: String? = null,
    val total: Int? = null,
)

@Serializable
data class Attachment(
    val part: Int,
    @Serializable(with = SafeTextSerializer::class) val filename: String = "attachment",
    val type: String = "application/octet-stream",
    val size: Long = 0,
    val inline: Boolean = false,
    /** An attached email (message/rfc822) the server could read: its card. A broken one has none
     *  and is listed with the files as a plain .eml file. */
    val email: AttachedEmailSummary? = null,
)

/** What an attached email's closed card shows. */
@Serializable
data class AttachedEmailSummary(
    @Serializable(with = SafeTextSerializer::class) val subject: String = "",
    @Serializable(with = SafeTextSerializer::class) @SerialName("from_name") val fromName: String = "",
    @Serializable(with = SafeAddressSerializer::class) @SerialName("from_email") val fromEmail: String = "",
    val date: String? = null,
    @Serializable(with = SafeSnippetSerializer::class) val excerpt: String = "",
    val attachments: Int = 0,
    /** It is an invitation: the server shows its card for a forward from a trusted address. */
    val invite: Boolean = false,
)

/** A file of an attached email: `part` is the outer message's part number (null: it cannot be
 *  downloaded on its own). */
@Serializable
data class AttachedFile(
    val part: Int? = null,
    @Serializable(with = SafeTextSerializer::class) val filename: String = "attachment",
    val type: String = "application/octet-stream",
    val size: Long = 0,
    val inline: Boolean = false,
    val email: AttachedEmailSummary? = null,
)

/** GET /messages/{id}/attachments/{part}/email: an attached email as the reader shows it
 *  (mail.open_attached_email), sanitised like a body. */
@Serializable
data class AttachedEmailBody(
    val part: Int = 0,
    @Serializable(with = SafeTextSerializer::class) val subject: String = "",
    val from: Address = Address(""),
    val to: List<Address> = emptyList(),
    val cc: List<Address> = emptyList(),
    val date: String? = null,
    val html: String? = null,
    val text: String? = null,
    @SerialName("remote_images") val remoteImages: Boolean = false,
    val source: String = "html",
    val proxied: Boolean = false,
    val junk: Boolean = false,
    val trackers: Trackers = Trackers(),
    @SerialName("links_unwrapped") val linksUnwrapped: Int = 0,
    val attachments: List<AttachedFile> = emptyList(),
)

@Serializable
data class Message(
    val id: Long,
    @SerialName("conversation_id") val conversationId: Long = 0,
    val from: Address = Address(""),
    val to: List<Address> = emptyList(),
    val cc: List<Address> = emptyList(),
    val bcc: List<Address> = emptyList(),
    @Serializable(with = SafeTextSerializer::class) val subject: String = "",
    val date: String? = null,
    @Serializable(with = SafeSnippetSerializer::class) val snippet: String = "",
    val seen: Boolean = true,
    val flagged: Boolean = false,
    val important: Boolean = false,
    val draft: Boolean = false,
    @SerialName("label_ids") val labelIds: List<Long> = emptyList(),
    val attachments: List<Attachment> = emptyList(),
    @SerialName("list_unsubscribe") val listUnsubscribe: String? = null,
    @SerialName("auth_results") val authResults: Map<String, String> = emptyMap(),
    @SerialName("sent_by_me") val sentByMe: Boolean = false,
    /** The body may be in a language the reader did not list as their own (backend core/langid.py). */
    @SerialName("offer_translate") val offerTranslate: Boolean = false,
    /** A move of one of my events this email announces in words (backend intel/event_changes.py); only while open. */
    @SerialName("event_change") val eventChange: EventChange? = null,
)

/**
 * A move of one of my events that an email announces in words ("Could we move our meeting to Oct 28?"),
 * as the web's EventChange: the event, its old and new times, and the sentence it came from.
 */
@Serializable
data class EventChange(
    val uid: String = "",
    @Serializable(with = SafeTextSerializer::class) val summary: String = "",
    @SerialName("old_start") val oldStart: String = "",
    @SerialName("old_end") val oldEnd: String? = null,
    @SerialName("new_start") val newStart: String = "",
    @SerialName("new_end") val newEnd: String = "",
    @Serializable(with = SafeTextSerializer::class) val sentence: String = "",
)

@Serializable
data class ConversationDetail(val conversation: ConversationSummary, val messages: List<Message>)

@Serializable
data class MessageBody(
    val html: String? = null,
    val text: String? = null,
    @SerialName("remote_images") val remoteImages: Boolean = false,
    @SerialName("has_quote") val hasQuote: Boolean = false,
    val source: String = "html",
    val highlighted: Boolean = false,
    /** Allowed images point at mailr's image proxy (backend core/imageproxy.py). */
    val proxied: Boolean = false,
    /** The message is in Spam: images load only after the reader confirms. */
    val junk: Boolean = false,
    /** Open-tracking images the server removed from the body. */
    val trackers: Trackers = Trackers(),
    @SerialName("links_unwrapped") val linksUnwrapped: Int = 0,
)

@Serializable
data class Trackers(val count: Int = 0, val domains: List<String> = emptyList())

@Serializable
data class SummaryResponse(
    val status: String = "unavailable",
    val summary: kotlinx.serialization.json.JsonElement? = null,
    @SerialName("action_items") val actionItems: List<String> = emptyList(),
    val reason: String? = null,
) {
    val lines: List<String>
        get() = when (val s = summary) {
            is kotlinx.serialization.json.JsonArray -> s.mapNotNull { (it as? kotlinx.serialization.json.JsonPrimitive)?.content }
            is kotlinx.serialization.json.JsonPrimitive -> if (s.isString) listOf(s.content) else emptyList()
            else -> emptyList()
        }
}

@Serializable
data class ActionRequest(
    val ids: List<Long>,
    val action: String,
    @SerialName("label_ids") val labelIds: List<Long>? = null,
    @SerialName("label_id") val labelId: Long? = null,
    val until: String? = null,
    val labels: Map<String, List<Long>>? = null,
    /** Archive from a label's list (e.g. a "Later" tab) also removes that label. */
    @SerialName("from_label_id") val fromLabelId: Long? = null,
    /** The undo of Mark important / not important (restore_importance): the toggle's record, sent back as the server gave it. */
    val importance: JsonObject? = null,
    /** The undo of a select-all toggle that ran as a job. */
    val job: Long? = null,
    /** Show in tab (ACT mail.show_in_tab): a tab id of the active layout (GET layouts/show-in-tab). */
    val tab: String? = null,
    /** The category action (ACT mail.category): primary, promotions, social, updates, forums or purchases. */
    val category: String? = null,
    /** The server's undo object as it came, so Undo sends it back unchanged (fields this app does not know too). */
    @kotlinx.serialization.Transient val raw: JsonObject? = null,
)

/** A tab of the active inbox layout as "Show in tab" offers it (backend core/tabplace.py). */
@Serializable
data class ShowInTabTarget(
    val id: String,
    val name: String,
    val icon: String? = null,
    /** first, label, category, inbox, or disabled (with [reason]). */
    val kind: String = "first",
    @SerialName("label_id") val labelId: Long? = null,
    val category: String? = null,
    val reason: String? = null,
) {
    val disabled: Boolean get() = kind == "disabled"
}

/** GET layouts/show-in-tab: the layout's tabs in tab-bar order and the one tab the conversations show in now. */
@Serializable
data class ShowInTabChoices(
    val layout: String? = null,
    val tabs: List<ShowInTabTarget> = emptyList(),
    val current: String? = null,
)

@Serializable
data class ActionResponse(
    val ok: Boolean = true,
    val affected: Int = 0,
    val undo: JsonObject? = null,
    val status: String? = null,
)

@Serializable
data class ComposeAttachment(val blob: String, val filename: String, val type: String, val size: Long = 0)

@Serializable
data class ComposePayload(
    @SerialName("identity_id") val identityId: Long? = null,
    val to: List<Address> = emptyList(),
    val cc: List<Address> = emptyList(),
    val bcc: List<Address> = emptyList(),
    val subject: String = "",
    val html: String? = null,
    val text: String? = null,
    @SerialName("in_reply_to_message_id") val inReplyToMessageId: Long? = null,
    @SerialName("forward_of_message_id") val forwardOfMessageId: Long? = null,
    val attachments: List<ComposeAttachment> = emptyList(),
    /** Extra `X-` headers; "Send to self" marks its mail as shared from another app. */
    val headers: Map<String, String> = emptyMap(),
    /** The user's own labels for the sent copy ("Send to self"); null leaves the key out. */
    @SerialName("label_ids") val labelIds: List<Long>? = null,
)

/**
 * A saved draft. [version] is its live version (core/live.py); [merged] says the server merged
 * this save with changes saved since its base version, and [payload] is then what it stored.
 */
@Serializable
data class DraftResponse(
    val draft: Message,
    val version: Int? = null,
    val merged: Boolean = false,
    val payload: JsonObject? = null,
)

@Serializable
data class DraftDetail(val draft: Message? = null, val payload: ComposePayload, val version: Int? = null)

/** GET /live/{kind}/{id}: an item's current version and editable content (live editing). */
@Serializable
data class LiveItem(
    val kind: String = "",
    val id: JsonElement? = null,
    val version: Int? = null,
    val content: JsonObject = JsonObject(emptyMap()),
    val by: String? = null,
)

@Serializable
data class SendRequest(
    val payload: ComposePayload,
    @SerialName("draft_id") val draftId: Long? = null,
    @SerialName("send_at") val sendAt: String? = null,
)

@Serializable
data class Outbox(
    val id: Long,
    val status: String,
    @SerialName("send_at") val sendAt: String? = null,
    val recipients: List<String> = emptyList(),
    val subject: String = "",
    @SerialName("token_name") val tokenName: String? = null,
    @SerialName("approval_reason") val approvalReason: String = "",
    val error: String = "",
    @SerialName("conversation_id") val conversationId: Long? = null,
)

@Serializable
data class OutboxResponse(val outbox: Outbox)

@Serializable
data class CancelResponse(val payload: ComposePayload? = null, @SerialName("draft_id") val draftId: Long? = null)

@Serializable
data class Person(
    @Serializable(with = SafeAddressSerializer::class) val email: String,
    @Serializable(with = SafeTextSerializer::class) val name: String = "",
    val organization: String = "",
    @SerialName("sent_count") val sentCount: Int = 0,
    @SerialName("received_count") val receivedCount: Int = 0,
)

@Serializable
data class Upload(val blob: String, val filename: String, val type: String, val size: Long = 0)

@Serializable
data class ChangeEntry(val entity: String, val id: Long, val op: String)

@Serializable
data class Changes(val cursor: Long = 0, val changes: List<ChangeEntry> = emptyList(), val more: Boolean = false)

/** [lines] is the plain-text fallback; [display] the typed display model (ui/present, agent/present.py). */
@Serializable
data class ApprovalPreview(
    val title: String = "",
    val lines: List<String> = emptyList(),
    val display: io.github.murtazanasir.mailr.ui.present.Display? = null,
    /** "review" on a bulk review's card (agent/review.py), with [review] its plan. */
    val kind: String? = null,
    val review: ReviewPreview? = null,
)

/**
 * A bulk review's card (agent/review.py `card`): the proposed moves grouped by move and sender, what
 * was set aside without the model ([setAside], by reason), what the model kept and what it was unsure
 * about (a few rows each, with their full counts).
 */
@Serializable
data class ReviewPreview(
    @SerialName("review_id") val reviewId: Long = 0,
    val scope: String = "",
    val total: Int = 0,
    val groups: List<ReviewGroup> = emptyList(),
    @SerialName("set_aside") val setAside: Map<String, Int> = emptyMap(),
    val kept: List<ReviewRow> = emptyList(),
    @SerialName("kept_count") val keptCount: Int = 0,
    val unsure: List<ReviewRow> = emptyList(),
    @SerialName("unsure_count") val unsureCount: Int = 0,
    /** The labels the review could add: the move menu offers them. */
    val labels: List<ReviewLabel> = emptyList(),
    /** On a decided card: how many conversations the user moved. */
    val changed: Int = 0,
)

/** A label a review card's move menu offers ("label:<id>" in `changes`). */
@Serializable
data class ReviewLabel(val id: Long = 0, val name: String = "")

/** One move of a review card (trash, archive, read, label) and the senders it covers. */
@Serializable
data class ReviewGroup(
    val move: String = "",
    val label: String? = null,
    /** A label move's label (the filter an "Always label …" makes adds it). */
    @SerialName("label_id") val labelId: Long? = null,
    val count: Int = 0,
    val senders: List<ReviewSender> = emptyList(),
)

/**
 * A sender in a review group: its conversations and up to three sample subjects. [email]: the one
 * address all of them came from; [suggestFilter]: the card offers "Always …" for their future mail.
 */
@Serializable
data class ReviewSender(
    val from: String = "",
    val count: Int = 0,
    @SerialName("conversation_ids") val conversationIds: List<Long> = emptyList(),
    val subjects: List<String> = emptyList(),
    val email: String? = null,
    @SerialName("suggest_filter") val suggestFilter: Boolean = false,
    /** Each of the sender's conversations, for the per-conversation move menu (cards from 20 Oct 2026 on). */
    val conversations: List<ReviewConversation>? = null,
)

/** One conversation of a sender on a review card: its subject and the date of its last message. */
@Serializable
data class ReviewConversation(
    @SerialName("conversation_id") val conversationId: Long = 0,
    val subject: String = "",
    val date: String? = null,
)

/** A conversation the review kept or was unsure about, with the model's reason. */
@Serializable
data class ReviewRow(
    @SerialName("conversation_id") val conversationId: Long = 0,
    val from: String = "",
    val subject: String = "",
    val reason: String = "",
    val email: String? = null,
    val date: String? = null,
    /** On a decided card: kept because the user chose Keep. */
    @SerialName("by_you") val byYou: Boolean = false,
)

/**
 * A bulk review's progress (GET /agent/reviews/{id}, agent/review.py `progress`): running, stopping,
 * stopped, done, failed or applied; [approvalId] is its review card once it is ready.
 */
@Serializable
data class ReviewState(
    @SerialName("review_id") val reviewId: Long = 0,
    val status: String = "running",
    val scope: String = "",
    val total: Int = 0,
    val done: Int = 0,
    @SerialName("set_aside") val setAside: Int = 0,
    @SerialName("approval_id") val approvalId: Long? = null,
    val error: String? = null,
) {
    /** Still working: the chat asks again every 2 s. */
    val live: Boolean get() = status == "running" || status == "stopping"
}

@Serializable
data class Approval(
    val id: Long,
    val subject: String = "",
    val actor: String = "",
    val action: String = "",
    val target: String = "",
    val preview: ApprovalPreview = ApprovalPreview(),
    val status: String = "pending",
    @SerialName("expires_at") val expiresAt: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    /** Set when this row came from the legacy outbox approval queue. */
    val outboxId: Long? = null,
    /** The display model and the outcome the card shows once decided (agent/present.py). */
    val display: io.github.murtazanasir.mailr.ui.present.Display? = null,
    val outcome: io.github.murtazanasir.mailr.ui.present.Outcome? = null,
    /** How many other waiting requests each grant on this card would approve too (pending cards only). */
    @SerialName("also_covers") val alsoCovers: AlsoCovers? = null,
    /** False when a hard rule held the card (the turn read mail, a forced ask): it is decided once, with no
     *  grants. An older server sends nothing: the grants show and the server refuses those it must. */
    val grants: Boolean = true,
    /** The assistant chat that asked, when one did ("Allow all" groups by it). */
    @SerialName("chat_session_id") val chatSessionId: String? = null,
) {
    /** Who asked, for "Allow all": one chat, else the agent token or the assistant (POST /approvals/decide-many). */
    val requesterKey: String get() = chatSessionId?.let { "chat:$it" } ?: subject

    /** The display model from the card or its preview, in the app's language, or null from an older server. */
    val shown: io.github.murtazanasir.mailr.ui.present.Display? get() = (display ?: preview.display)?.localized()

    /** A bulk review's plan when this is a review card (`preview.kind == "review"`), else null. */
    val review: ReviewPreview? get() = preview.review?.takeIf { preview.kind == "review" }
}

/**
 * For a waiting card: how many OTHER waiting requests each grant would approve as well.
 * [session] is "Allow for 12 hours" (or for this chat), [always]
 * "Always allow" for every such action, [alwaysDerived] "Always allow" narrowed to this request.
 */
@Serializable
data class AlsoCovers(
    val session: Int = 0,
    val always: Int = 0,
    @SerialName("always_derived") val alwaysDerived: Int = 0,
)

/** The decide response's grant: its rule (for Undo) and the other waiting requests it decided. */
@Serializable
data class DecideResult(
    val status: String = "",
    @SerialName("rule_id") val ruleId: Long? = null,
    @SerialName("also_decided") val alsoDecided: AlsoDecided? = null,
)

@Serializable
data class AlsoDecided(val ids: List<Long> = emptyList(), val count: Int = 0)

/** Undo of a grant: the requests back to waiting, and those that had already run. */
@Serializable
data class UndoGrantResult(val reverted: List<Long> = emptyList(), val kept: List<Long> = emptyList())

@Serializable
data class SessionInfo(
    val id: String,
    val device: String = "web",
    @SerialName("user_agent") val userAgent: String = "",
    val ip: String = "",
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("last_seen_at") val lastSeenAt: String = "",
    val current: Boolean = false,
)

@Serializable
data class Citation(
    val n: Int,
    val kind: String = "message",
    @SerialName("conversation_id") val conversationId: Long? = null,
    @SerialName("message_id") val messageId: Long? = null,
    val subject: String? = null,
    val from: String? = null,
    val date: String? = null,
    val quote: String? = null,
    val verified: Boolean = true,
    /** `recording`: a Speakr recording or a moment of it ([[r:id@seconds "quote"]]); [t] is that moment. */
    @SerialName("recording_id") val recordingId: Long? = null,
    @SerialName("connector_id") val connectorId: Long? = null,
    val t: Double? = null,
    val title: String? = null,
    /** An event or task: whether [date] is a whole day. */
    @SerialName("all_day") val allDay: Boolean = false,
    val speaker: String? = null,
    /** Speakr's deep link at the moment, for a recording; the page, for a `web` citation. */
    val url: String? = null,
    /** `web`: a page the assistant's web search found ([[web:url]]): its site (host without www). */
    val site: String? = null,
    val provider: String? = null,
    /** `note`: a note or one item of a list ([[note:id#item]]): the note, the item, a line of it. */
    @SerialName("note_id") val noteId: String? = null,
    @SerialName("item_id") val itemId: String? = null,
    @SerialName("note_kind") val noteKind: String? = null,
    val snippet: String? = null,
    /** `table`: a table, or one row of it ([[table:id#row]]). */
    @SerialName("table") val tableId: String? = null,
    @SerialName("row") val rowId: String? = null,
    /** `event` or `task`: the record, and an event's occurrence. */
    val uid: String? = null,
    val occurrence: String? = null,
    /** `event` or `task`: the record's colour, else its calendar's (shown beside the calendar's name). */
    val color: String? = null,
) {
    /** The record this citation points to, for the Sources list (the web's sourceKey in
     *  citations.ts; cases in spec/cases/source-cases.yaml): every citation of one email thread
     *  is one source, so is every citation of one event occurrence, task, note item, table row,
     *  recording or web page. */
    val sourceKey: String get() = when {
        kind == "table" && tableId != null -> "t:$tableId/${rowId.orEmpty()}"
        kind == "web" && url != null -> "w:$url"
        kind == "note" && noteId != null -> "n:$noteId#${itemId.orEmpty()}"
        kind == "recording" && recordingId != null -> "r:${connectorId ?: ""}:$recordingId"
        (kind == "event" || kind == "task") && uid != null -> "$kind:$uid@${occurrence.orEmpty()}"
        conversationId != null -> "c:$conversationId"
        messageId != null -> "m:$messageId"
        else -> "#$n"
    }

    val isRecording: Boolean get() = kind == "recording" && recordingId != null && connectorId != null
    /** A note or one item of a list: it opens in the app's Notes screen. */
    val isNote: Boolean get() = kind == "note" && noteId != null
    /** A web page with an http(s) address: it opens in the browser, outside mailr. */
    val isWeb: Boolean get() = kind == "web" && url != null &&
        (url.startsWith("https://", ignoreCase = true) || url.startsWith("http://", ignoreCase = true))
}

/** A generic JSON value for fields the app only passes through. */
typealias Json = JsonElement

/** Inbox layouts: optional tabs, each with one or more sections. */
@Serializable
data class LayoutSection(
    val id: String,
    val title: String,
    val base: String = "inbox",
    val category: String? = null,
    val filter: String? = null,
    val q: String? = null,
    val limit: Int? = null,
    @SerialName("exclude_above") val excludeAbove: Boolean = true,
)

/** One tab's counts from GET /layouts/{id}/counts?detail=true (only the tab's indicators). */
@Serializable
data class TabCounts(
    val unread: Int? = null,
    val all: Int? = null,
    @SerialName("due_count") val dueCount: Int? = null,
    @SerialName("earliest_due") val earliestDue: String? = null,
    @SerialName("earliest_due_from") val earliestDueFrom: String? = null,
)

@Serializable
data class TabCountsResponse(val tabs: Map<String, TabCounts> = emptyMap())

@Serializable
data class LayoutTab(
    val id: String,
    val name: String,
    val icon: String? = null,
    val badge: String = "none",
    val sections: List<LayoutSection> = emptyList(),
    /** What the tab shows by its name: "unread", "all", "due". Null from an
     *  older server: the badge alone. */
    val indicators: List<String>? = null,
) {
    /** The section paged as the main list: the last one without a limit (or the last one). */
    val main: LayoutSection? get() = sections.lastOrNull { it.limit == null } ?: sections.lastOrNull()
}

@Serializable
data class InboxLayout(val id: String, val name: String, val preset: Boolean = false, val tabs: List<LayoutTab> = emptyList())

@Serializable
data class LayoutsResponse(val active: String = "default", val layouts: List<InboxLayout> = emptyList()) {
    val activeLayout: InboxLayout? get() = layouts.firstOrNull { it.id == active } ?: layouts.firstOrNull { it.id == "default" }
}

/** One run of a standing order or routine (GET /assistant/runs). */
@Serializable
data class AssistantRun(
    val id: Long,
    @SerialName("order_name") val orderName: String? = null,
    /** "forwarded_notes" for the built-in order Follow my forwarded notes: shown by its translated name. */
    @SerialName("order_builtin") val orderBuiltin: String? = null,
    val status: String = "",
    @SerialName("conversation_id") val conversationId: Long? = null,
    val output: String = "",
    val delivered: List<String> = emptyList(),
    val citations: List<Citation> = emptyList(),
    @SerialName("finished_at") val finishedAt: String? = null,
    /** On a conversation's brief: the approvals the run asked for, with their state now. */
    val approvals: List<RunApproval> = emptyList(),
)

/** An approval a run asked for. [status]: pending, approved, executing, executed, denied, expired or failed;
 *  [result] is the outcome's title once it ran ("Draft saved"). */
@Serializable
data class RunApproval(
    val title: String = "",
    val status: String = "",
    val url: String = "",
    val result: String? = null,
) {
    val open: Boolean get() = status in setOf("pending", "approved", "executing")
    /** The approval's key, the last part of its link (mailr://approvals/<key> in the app). */
    val key: String get() = url.trimEnd('/').substringAfterLast('/')
}

/** GET /conversations/{id}/brief: the latest brief a standing order or routine left on it. */
@Serializable
data class BriefResponse(val brief: AssistantRun? = null)

/** A push target on mailr (POST /push/targets). */
@Serializable
data class PushTarget(
    val id: Long,
    val kind: String = "unifiedpush",
    val endpoint: String = "",
    val device: String = "",
    val events: List<String> = emptyList(),
    val encrypted: Boolean = false,
    /** Registered with mailr's VAPID key: the messages carry its signature. */
    val vapid: Boolean = false,
)

/** GET /push/targets: the key a phone registers its endpoint with (absent on an older server). */
@Serializable
data class PushTargets(
    val events: List<String> = emptyList(),
    @SerialName("vapid_public_key") val vapidPublicKey: String? = null,
)

/** POST /push/targets/{id}/test: whether the push server took it, and why not. */
@Serializable
data class PushTestAnswer(val ok: Boolean = false, val error: String? = null, val removed: Boolean = false)

/** POST /approvals/decide-many: how many were decided, and the ones already decided elsewhere. */
@Serializable
data class DecideManyResult(
    val count: Int = 0,
    val skipped: List<SkippedApproval> = emptyList(),
)

@Serializable
data class SkippedApproval(val id: Long = 0, val status: String = "")

/** One row of an answer's Sources: a cited record and every number that cites it ([first] opens it). */
data class SourceRow(val key: String, val first: Citation, val all: List<Citation>)

/** The Sources list: one row per cited record, in the order of first citation. The numbers in the
 *  text stay as they are (the web's sourceRows). */
fun sourceRows(cites: List<Citation>): List<SourceRow> =
    cites.sortedBy { it.n }.groupBy { it.sourceKey }.map { (k, all) -> SourceRow(k, all.first(), all) }

/** A memory note as the "Used memories" line shows it (GET /assistant/memories?ids=…). */
@Serializable
data class MemoryNote(
    val id: Long,
    @Serializable(with = SafeTextSerializer::class) val text: String = "",
    val kind: String = "fact",
    val entity: MemoryAbout? = null,
)

@Serializable
data class MemoryAbout(
    val id: Long,
    @Serializable(with = SafeTextSerializer::class) val name: String = "",
    val kind: String = "person",
    @kotlinx.serialization.SerialName("is_self") val isSelf: Boolean = false,
)

@Serializable
data class MemoryNotes(val items: List<MemoryNote> = emptyList(), val missing: Int = 0)
