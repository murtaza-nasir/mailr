package io.github.murtazanasir.mailr.ui.assistant

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.agent.stepEmail
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.ui.present.ServerText
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull

/**
 * A chat reopened from the server (GET /agent/sessions/{id}, agent/routes.py `timeline`) as the turns
 * the screen shows, the same way the web rebuilds them (timeline.ts fromReplay, useAssistant.ts
 * fromReplayTurns): each assistant turn's timeline of text, steps, approval cards and resume lines,
 * its citations and the memories that went into its prompt (the "Used memories" line).
 */
object SessionReplay {
    fun turns(session: JsonObject): List<ChatTurn> {
        val raw = (session["turns"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
        val out = raw.map { t ->
            if (str(t, "role") == "user") ChatTurn("user", str(t, "content").orEmpty(), streaming = false, messageId = (t["id"] as? JsonPrimitive)?.longOrNull)
            else assistant(t)
        }
            .toMutableList()
        // Waiting cards the timeline does not hold (an older server): on the last reply.
        val placed = out.flatMap { it.approvals.map { a -> a.id } }.toSet()
        val pending = (session["pending_approvals"] as? JsonArray).orEmpty().mapNotNull { card(it as? JsonObject) }.filter { it.id !in placed }
        val last = out.indexOfLast { it.role == "assistant" }
        if (last >= 0 && pending.isNotEmpty()) out[last] = out[last].copy(approvals = out[last].approvals + pending)
        // The chat's unsettled bulk reviews (`reviews`): their progress lines (and cards) on the last reply, as on the web.
        val reviews = (session["reviews"] as? JsonArray).orEmpty().mapNotNull { ((it as? JsonObject)?.get("review_id") as? JsonPrimitive)?.longOrNull }
        if (last >= 0 && reviews.isNotEmpty()) out[last] = out[last].copy(reviews = (out[last].reviews + reviews).distinct())
        return out
    }

    /** The chat's last turn still runs at the server (`running`): the screen follows it again. */
    fun running(session: JsonObject): Boolean = (session["running"] as? JsonPrimitive)?.booleanOrNull == true

    private fun assistant(t: JsonObject): ChatTurn {
        val timeline = (t["timeline"] as? JsonArray)?.mapNotNull { it as? JsonObject }
        val content = str(t, "content").orEmpty()
        val items = mutableListOf<TurnItem>()
        val approvals = mutableListOf<Approval>()
        if (timeline == null) {
            val steps = steps(t["steps"] as? JsonArray)
            if (steps.isNotEmpty()) items += TurnItem.Steps(0, steps)
            if (content.isNotEmpty()) items += TurnItem.Text(1, content, content)
        } else {
            var n = 0
            for (i in timeline) {
                when (str(i, "type")) {
                    "steps" -> items += TurnItem.Steps(n++, steps(i["steps"] as? JsonArray))
                    "approval" -> card(i["approval"] as? JsonObject)?.let { a -> approvals += a; items += TurnItem.Approval(n, a.id) }
                    "resume" -> items += TurnItem.Resume(n, str(i, "text") ?: AppText.get(R.string.asst_after_approval))
                    else -> str(i, "content").orEmpty().let { c -> items += TurnItem.Text(n, c, c) }
                }
            }
        }
        val cites = catching {
            MailrJson.decodeFromJsonElement(ListSerializer(Citation.serializer()), t["citations"] ?: JsonArray(emptyList()))
        }.getOrDefault(emptyList())
        val memoryIds = (t["memory_ids"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.longOrNull }
        return ChatTurn("assistant", items = items, citations = cites, approvals = approvals, memoryIds = memoryIds,
            error = if (str(t, "status") == "error") AppText.get(R.string.asst_failed) else null, streaming = false, startedAt = 0, finishedAt = 0,
            interrupted = str(t, "status") == "interrupted")
    }

    private fun steps(a: JsonArray?): List<Step> = a.orEmpty().mapNotNull { it as? JsonObject }.map { s ->
        val failed = (str(s, "status") ?: if ((s["ok"] as? JsonPrimitive)?.booleanOrNull == false) "failed" else "done") == "failed"
        Step(id = str(s, "id").orEmpty(), name = str(s, "name").orEmpty(),
            label = ServerText.text(s["label_msg"], str(s, "label") ?: str(s, "name").orEmpty()),
            done = true, ok = !failed, summary = ServerText.text(s["summary_msg"], str(s, "summary").orEmpty()),
            count = (s["count"] as? JsonPrimitive)?.intOrNull, icon = str(s, "icon"),
            approvalId = (s["approval_id"] as? JsonPrimitive)?.longOrNull, repeatOf = str(s, "repeat_of")?.ifBlank { null },
            email = stepEmail(s))
    }

    private fun card(o: JsonObject?): Approval? = o?.let { catching { MailrJson.decodeFromJsonElement(Approval.serializer(), it) }.getOrNull() }

    private fun str(o: JsonObject, k: String): String? = (o[k] as? JsonPrimitive)?.contentOrNull
}
