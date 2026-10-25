package io.github.murtazanasir.mailr.data.api

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import android.content.ContentResolver
import android.net.Uri
import android.provider.OpenableColumns
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okio.BufferedSink
import okio.source

/** Typed endpoints. Every mutation takes an idempotency key so callers can retry safely. */
class MailrApi(val client: ApiClient) {

    // ---- auth (the shared sign-in routes, android/session-core) ----
    val auth = AuthApi(client)
    suspend fun authConfig() = auth.authConfig()
    suspend fun nativeConfig() = auth.nativeConfig()
    suspend fun nativeSignIn(idToken: String, device: String) = auth.nativeSignIn(idToken, device)
    suspend fun passwordSignIn(email: String, password: String) = auth.passwordSignIn(email, password)
    suspend fun signOut() = auth.signOut()

    suspend fun me() = client.get("me", Me.serializer())

    /** PATCH /me/settings: merges the given keys; the answer is every setting after the change. */
    suspend fun patchSettings(body: JsonObject) = client.send(
        "PATCH", "me/settings", JsonObject.serializer(), MailrJson.encodeToString(JsonObject.serializer(), body),
        idempotencyKey = null,
    )
    suspend fun sessions() = client.get("me/sessions", ListSerializer(SessionInfo.serializer()))
    suspend fun revokeSession(id: String) =
        client.send("DELETE", "me/sessions/$id", JsonObject.serializer(), idempotencyKey = null)

    // ---- mail ----
    suspend fun labels() = client.get("labels", ListSerializer(Label.serializer()))

    /**
     * POST /labels: a new label. With [homeAccountId] it is a folder of that mail account ("New folder"
     * in an account's group): listed there at once, made on that account's server and pinned to the
     * group by the server. ACT mail.account_folder_create @android.drawer
     */
    suspend fun createLabel(name: String, homeAccountId: Long? = null) = client.send(
        "POST", "labels", Label.serializer(),
        MailrJson.encodeToString(JsonObject.serializer(), buildJsonObject {
            put("name", name)
            if (homeAccountId != null) put("home_account_id", homeAccountId)
        }),
    )
    /** GET /inbox/count (ACT settings.inbox_count @android.drawer): the number by Inbox. */
    suspend fun inboxCount() = client.get("inbox/count", InboxCount.serializer())

    suspend fun conversations(
        view: String,
        q: String? = null,
        section: String? = null,
        sections: String? = null,
        cursor: String? = null,
        limit: Int = 40,
        category: String? = null,
        layout: String? = null,
        tab: String? = null,
        sec: String? = null,
    ) = client.get(
        "conversations", Page.serializer(ConversationSummary.serializer()),
        mapOf("view" to view, "q" to q?.takeIf { it.isNotBlank() }, "section" to section,
            "sections" to sections, "cursor" to cursor, "limit" to limit, "category" to category,
            "layout" to layout, "tab" to tab, "sec" to sec),
    )

    /** GET /counts: the mail accounts and their own folders' numbers (MailAccounts.kt). */
    suspend fun counts() = client.get("counts", SidebarCounts.serializer())

    suspend fun layouts() = client.get("layouts", LayoutsResponse.serializer())
    suspend fun layoutCounts(id: String) =
        client.get("layouts/$id/counts", MapSerializer(String.serializer(), Int.serializer()))
    suspend fun layoutIndicators(id: String) =
        client.get("layouts/$id/counts", TabCountsResponse.serializer(), mapOf("detail" to "true")).tabs

    suspend fun conversation(id: Long) = client.get("conversations/$id", ConversationDetail.serializer())

    suspend fun body(messageId: Long, imagesAllowed: Boolean, quote: String? = null): MessageBody =
        if (quote != null) {
            catching {
                client.get("messages/$messageId/body-highlight", MessageBody.serializer(),
                    mapOf("q" to quote, "images" to if (imagesAllowed) "allowed" else "blocked"))
            }.getOrElse { body(messageId, imagesAllowed, null) }
        } else {
            client.get("messages/$messageId/body", MessageBody.serializer(),
                mapOf("images" to if (imagesAllowed) "allowed" else "blocked"))
        }

    /**
     * A remote image of a message as mailr's image proxy serves it (GET messages/{id}/images/{hash}),
     * as (type, bytes); null when the server has none. Blocking: the message WebView calls it on
     * its loader thread. ACT mail.show_images @android.reader
     */
    fun proxiedImage(path: String): Pair<String, ByteArray>? {
        val url = client.baseUrl().resolve(path.removePrefix("/api/v1/")) ?: return null
        return client.http.newCall(Request.Builder().url(url).get().build()).execute().use { r ->
            val type = r.header("Content-Type")?.substringBefore(';')?.trim()?.lowercase()
            if (!r.isSuccessful || type == null || !type.startsWith("image/") || "svg" in type) null
            else r.body?.bytes()?.let { type to it }
        }
    }

    /** What "Show in tab" offers for these conversations (ACT mail.show_in_tab). */
    suspend fun showInTabChoices(ids: List<Long>) =
        client.get("layouts/show-in-tab", ShowInTabChoices.serializer(), mapOf("conversation_ids" to ids.joinToString(",")))

    suspend fun conversationAction(req: ActionRequest, key: String) =
        client.send("POST", "conversations/actions", ActionResponse.serializer(), MailrJson.encodeToString(req), key)

    suspend fun messageAction(req: ActionRequest, key: String) =
        client.send("POST", "messages/actions", ActionResponse.serializer(), MailrJson.encodeToString(req), key)

    suspend fun changes(since: Long) = client.get("changes", Changes.serializer(), mapOf("since" to since))

    // ---- compose ----
    suspend fun replyPrefill(messageId: Long, mode: String): ComposePayload {
        val el = client.get("compose/reply", JsonObject.serializer(), mapOf("message_id" to messageId, "mode" to mode))
        val inner = el["payload"]?.jsonObject ?: el
        return MailrJson.decodeFromJsonElement(inner)
    }

    /**
     * Create or replace a draft. [baseVersion], the live version the editor started from, lets
     * the server merge this save with changes saved since (a merged answer carries the stored
     * payload; an overlap answers 409 with both copies). Without it the payload replaces the draft.
     */
    suspend fun saveDraft(payload: ComposePayload, draftId: Long?, key: String, baseVersion: Int? = null): DraftResponse {
        val body = MailrJson.encodeToString(buildJsonObject {
            put("payload", MailrJson.encodeToJsonElement(ComposePayload.serializer(), payload))
            if (draftId != null && baseVersion != null) put("base_version", baseVersion)
        })
        return if (draftId == null) client.send("POST", "drafts", DraftResponse.serializer(), body, key)
        else client.send("PUT", "drafts/$draftId", DraftResponse.serializer(), body, key)
    }

    suspend fun draft(id: Long) = client.get("drafts/$id", DraftDetail.serializer())

    // ---- live editing (core/live.py) ----
    /** GET /live/{kind}/{id}: the item's current version and editable content. */
    suspend fun liveItem(kind: String, id: String) = client.get("live/$kind/$id", LiveItem.serializer())

    /** POST /live/typing: the user is typing in this item's editor (at most about once a second). */
    suspend fun liveTyping(kind: String, id: String) = client.send(
        "POST", "live/typing", JsonObject.serializer(),
        MailrJson.encodeToString(buildJsonObject { put("kind", kind); put("id", id) }), idempotencyKey = null,
    )
    suspend fun deleteDraft(id: Long) = client.send("DELETE", "drafts/$id", JsonObject.serializer())

    suspend fun send(req: SendRequest, key: String) =
        client.send("POST", "send", OutboxResponse.serializer(), MailrJson.encodeToString(req), key)

    suspend fun cancelOutbox(id: Long) =
        client.send("POST", "outbox/$id/cancel", CancelResponse.serializer())

    suspend fun outbox(status: String) =
        client.get("outbox", ListSerializer(Outbox.serializer()), mapOf("status" to status))

    suspend fun people(q: String) =
        client.get("people", ListSerializer(Person.serializer()), mapOf("q" to q, "limit" to 8))

    suspend fun upload(resolver: ContentResolver, uri: Uri): Upload {
        var name = "attachment"
        var size = -1L
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
            if (c.moveToFirst()) {
                name = c.getString(0) ?: name
                size = if (c.isNull(1)) -1 else c.getLong(1)
            }
        }
        val type = resolver.getType(uri) ?: "application/octet-stream"
        if (size > MAX_UPLOAD) throw ApiException(413, AppText.get(R.string.api_upload_too_big, name))
        val part = object : RequestBody() {
            override fun contentType() = type.toMediaTypeOrNull()
            // The provider's size is only a claim: the body is streamed with no length and cut off past the limit.
            override fun contentLength() = -1L
            // Read once: a retry would read the stream again.
            override fun isOneShot() = true
            override fun writeTo(sink: BufferedSink) {
                val input = resolver.openInputStream(uri) ?: throw java.io.IOException(AppText.get(R.string.api_upload_cant_read, name))
                input.use {
                    val buf = ByteArray(64 * 1024)
                    var total = 0L
                    while (true) {
                        val n = it.read(buf)
                        if (n < 0) break
                        total += n
                        if (total > MAX_UPLOAD) throw UploadTooLargeException(name)
                        sink.write(buf, 0, n)
                    }
                }
            }
        }
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("file", name, part).build()
        return client.execute(Request.Builder().url(client.url("uploads")).post(body).build(), Upload.serializer())
    }

    /** Upload a file the app staged in its own storage (shares copied before the grant expires). */
    suspend fun uploadFile(file: java.io.File, name: String, type: String): Upload {
        val part = object : RequestBody() {
            override fun contentType() = type.toMediaTypeOrNull()
            override fun contentLength() = file.length()
            override fun writeTo(sink: BufferedSink) {
                file.inputStream().use { sink.writeAll(it.source()) }
            }
        }
        val body = MultipartBody.Builder().setType(MultipartBody.FORM).addFormDataPart("file", name, part).build()
        return client.execute(Request.Builder().url(client.url("uploads")).post(body).build(), Upload.serializer())
    }

    /** Upload bytes the app prepared itself: an inline photo, reduced like the web's
     *  (ACT mail.insert_image @android.editor). Same multipart form as the web's uploadFile ("file"). */
    suspend fun uploadBytes(bytes: ByteArray, name: String, type: String): Upload {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("file", name, bytes.toRequestBody(type.toMediaTypeOrNull())).build()
        return client.execute(Request.Builder().url(client.url("uploads")).post(body).build(), Upload.serializer())
    }

    /**
     * A dictated clip to the speech server through mailr (POST /assistant/transcribe). [context]
     * names what it is spoken into (DictationContext); null lets the server keep it local. The clip is
     * not kept anywhere.
     */
    suspend fun transcribe(file: java.io.File, durationMs: Long, language: String?, context: String? = null): String {
        val part = object : RequestBody() {
            override fun contentType() = "audio/mp4".toMediaTypeOrNull()
            override fun contentLength() = file.length()
            override fun writeTo(sink: BufferedSink) {
                file.inputStream().use { sink.writeAll(it.source()) }
            }
        }
        val body = MultipartBody.Builder().setType(MultipartBody.FORM).addFormDataPart("file", "clip.m4a", part)
            .addFormDataPart("duration_ms", durationMs.toString())
            .apply { language?.let { addFormDataPart("language", it) } }
            .apply { context?.let { addFormDataPart("context", it) } }.build()
        return client.execute(Request.Builder().url(client.url("assistant/transcribe")).post(body).build(), Transcription.serializer()).text
    }

    fun attachmentUrl(messageId: Long, part: Int) = client.url("messages/$messageId/attachments/$part")

    /** ACT mail.open_attached_email @android.reader: an attached email, sanitised like a body. */
    suspend fun attachedEmail(messageId: Long, part: Int, imagesAllowed: Boolean): AttachedEmailBody =
        client.get("messages/$messageId/attachments/$part/email", AttachedEmailBody.serializer(),
            mapOf("images" to if (imagesAllowed) "allowed" else "blocked"))

    // ---- assistant runs (standing orders and routines) ----
    suspend fun assistantRuns(): List<AssistantRun> = catching {
        val el = client.get("assistant/runs", kotlinx.serialization.json.JsonElement.serializer(), mapOf("limit" to "20"))
        val arr = (el as? JsonObject)?.get("items")?.jsonArray ?: return@catching emptyList()
        arr.map { MailrJson.decodeFromJsonElement(AssistantRun.serializer(), it) }
    }.getOrDefault(emptyList())

    // ---- approvals (permission engine, with the older outbox queue as a fallback) ----
    suspend fun approvals(): List<Approval> = catching {
        val el = client.get("approvals", kotlinx.serialization.json.JsonElement.serializer(), mapOf("status" to "pending"))
        val arr = (el as? JsonObject)?.get("items")?.jsonArray ?: el.jsonArray
        arr.map { MailrJson.decodeFromJsonElement(Approval.serializer(), it) }
    }.getOrElse { e ->
        if (e is ApiException && e.code == 404) {
            outbox("awaiting_approval").map {
                Approval(id = it.id, actor = it.tokenName ?: AppText.get(R.string.api_approval_agent), action = "mail.send",
                    target = it.recipients.joinToString(), status = "pending", outboxId = it.id,
                    preview = ApprovalPreview(AppText.get(R.string.api_approval_send_subject, it.subject), listOfNotNull(
                        AppText.get(R.string.api_approval_to, it.recipients.joinToString()), it.approvalReason.ifBlank { null })))
            }
        } else throw e
    }

    /** The notes an answer used, in that order (the "Used memories" line); `missing` were deleted since. */
    suspend fun memoriesById(ids: List<Long>): MemoryNotes =
        client.get("assistant/memories", MemoryNotes.serializer(), mapOf("ids" to ids.joinToString(",")))

    suspend fun updateMemory(id: Long, text: String) = client.send("PATCH", "assistant/memories/$id", JsonObject.serializer(),
        MailrJson.encodeToString(JsonObject.serializer(), JsonObject(mapOf("text" to kotlinx.serialization.json.JsonPrimitive(text)))))

    suspend fun deleteMemory(id: Long) = client.send("DELETE", "assistant/memories/$id", JsonObject.serializer())

    /** One approval as a card (display model and outcome, agent/present.py). */
    suspend fun approval(id: Long): Approval = client.get("approvals/$id", Approval.serializer())

    /**
     * Decide one approval. A grant ("session", "always") also decides the other waiting requests it
     * covers; the result names them and the rule (null for the older outbox queue).
     */
    suspend fun decide(a: Approval, approve: Boolean, scope: String = "once", exceptIds: Collection<Long> = emptyList(),
                       changes: Map<Long, String> = emptyMap()): DecideResult? {
        if (a.outboxId != null) {
            client.send("POST", "outbox/${a.outboxId}/${if (approve) "approve" else "reject"}", JsonObject.serializer())
            return null
        }
        return client.send("POST", "approvals/${a.id}/decide", DecideResult.serializer(), MailrJson.encodeToString(
            decideBody(approve, scope, exceptIds, changes)))
    }

    /**
     * ACT approval.approve_many: "Allow all n" for several waiting requests from one chat or agent. Each is
     * decided once, as its own card would be; no rule is stored. Returns how many were decided.
     */
    suspend fun decideMany(ids: List<Long>, approve: Boolean = true): DecideManyResult =
        client.send("POST", "approvals/decide-many", DecideManyResult.serializer(), MailrJson.encodeToString(buildJsonObject {
            put("ids", kotlinx.serialization.json.JsonArray(ids.map { kotlinx.serialization.json.JsonPrimitive(it) }))
            put("decision", if (approve) "approve" else "deny")
        }))

    /** ACT approval.undo_grant: delete a grant's rule and put back the requests it decided that have not run. */
    suspend fun undoGrant(ruleId: Long): UndoGrantResult =
        client.send("POST", "approvals/grants/$ruleId/undo", UndoGrantResult.serializer())

    // ---- push (UnifiedPush; data/push) ----
    /**
     * POST /push/targets: this phone's UnifiedPush endpoint, its name, the events it wants and the
     * Web Push keys the connector made (mailr then encrypts every message for this phone). The
     * same endpoint again updates the row; a new endpoint is a new target.
     */
    suspend fun addPushTarget(endpoint: String, device: String, events: List<String>, p256dh: String?, auth: String?,
                              vapid: Boolean = false): PushTarget =
        client.send("POST", "push/targets", PushTarget.serializer(), MailrJson.encodeToString(buildJsonObject {
            put("endpoint", endpoint); put("kind", "unifiedpush"); put("device", device)
            put("events", kotlinx.serialization.json.JsonArray(events.map { kotlinx.serialization.json.JsonPrimitive(it) }))
            if (p256dh != null && auth != null) put("keys", buildJsonObject { put("p256dh", p256dh); put("auth", auth) })
            // The endpoint was registered with mailr's VAPID key: mailr signs every message to it.
            if (vapid) put("vapid", true)
        }))

    /**
     * GET /push/targets: mailr's VAPID public key (base64url, uncompressed P-256 point), which the
     * distributor registration carries so the push server accepts only messages mailr signed.
     * Null from a server without one (before migration b7d2e94c1f60).
     */
    suspend fun pushTargets(): PushTargets = client.get("push/targets", PushTargets.serializer())

    suspend fun deletePushTarget(id: Long) = client.send("DELETE", "push/targets/$id", JsonObject.serializer())

    suspend fun testPushTarget(id: Long) = client.send("POST", "push/targets/$id/test", PushTestAnswer.serializer())

    companion object {
        val strings = ListSerializer(String.serializer())

        /**
         * The body of POST /approvals/{id}/decide. [exceptIds]: on a cleanup plan, the conversations left
         * out when approving (the server only ever removes ids from the card). [changes]: on a review card,
         * the user's own move per conversation ("trash", "archive", "read", "keep" or "label:<id>"; the
         * server's apply_changes accepts only conversations the card showed).
         */
        fun decideBody(approve: Boolean, scope: String, exceptIds: Collection<Long> = emptyList(),
                       changes: Map<Long, String> = emptyMap()): JsonObject = buildJsonObject {
            put("decision", if (approve) "approve" else "deny")
            put("scope", scope)
            if (approve && exceptIds.isNotEmpty()) {
                put("except_ids", kotlinx.serialization.json.JsonArray(exceptIds.distinct().map { kotlinx.serialization.json.JsonPrimitive(it) }))
            }
            if (approve && changes.isNotEmpty()) put("changes", buildJsonObject { changes.forEach { (id, m) -> put(id.toString(), m) } })
        }

        /** The server's attachment limit (api/mail_write.py MAX_UPLOAD). */
        const val MAX_UPLOAD = 35L * 1024 * 1024
    }
}
