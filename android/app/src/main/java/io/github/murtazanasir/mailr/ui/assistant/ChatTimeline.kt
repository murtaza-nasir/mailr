package io.github.murtazanasir.mailr.ui.assistant

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.agent.AgentEvent
import io.github.murtazanasir.mailr.ui.present.CiteGrammar

/*
 * An assistant turn as an ordered timeline: text, the tool
 * steps the model ran, more text, the approval card a step asked for, and after a decision a resume
 * line and the continuation. Every server event carries `segment`, its position in the turn; text of
 * a segment comes before that segment's steps. Nothing is replaced: interim text stays where it was
 * written and the final answer finalises the last segment. The web folds events the same way
 * (frontend/src/features/rail/assistant/timeline.ts).
 */

data class Step(
    val id: String,
    val name: String,
    val label: String,
    val done: Boolean = false,
    val ok: Boolean = true,
    val summary: String = "",
    val count: Int? = null,
    /** The server's icon family (agent/present.py step_icon), or null from an older server. */
    val icon: String? = null,
    /** Set on the step that asked for an approval; it waits until the user decides. */
    val approvalId: Long? = null,
    val waiting: Boolean = false,
    /** The step this one repeated with the same arguments: its line folds into that step's ("×3"). */
    val repeatOf: String? = null,
    /** The address the step was about (`args.email`), for a person lookup's card. */
    val email: String? = null,
)

/** A line of a steps group: a step and how many times it was asked for (1 + its repeats). */
data class StepRow(val step: Step, val times: Int)

sealed interface TurnItem {
    val segment: Int

    /**
     * [final] is null while the text streams; set when the server finalises it (with citations).
     * [shown] never holds raw citation markup: the server sends numbered links, and anything raw
     * that is left (an older answer, a marker still being typed) is cleaned up (CiteGrammar).
     */
    data class Text(override val segment: Int, val raw: String, val final: String? = null) : TurnItem {
        val shown: String get() = if (final != null) CiteGrammar.clean(final) else CiteGrammar.clean(raw, streaming = true)
    }

    data class Steps(override val segment: Int, val steps: List<Step>) : TurnItem
    data class Approval(override val segment: Int, val id: Long) : TurnItem
    data class Resume(override val segment: Int, val text: String) : TurnItem
}

object ChatTimeline {
    private fun withText(items: List<TurnItem>, segment: Int, f: (TurnItem.Text) -> TurnItem.Text): List<TurnItem> {
        val i = items.indexOfFirst { it is TurnItem.Text && it.segment == segment }
        if (i >= 0) return items.toMutableList().also { it[i] = f(items[i] as TurnItem.Text) }
        val item = f(TurnItem.Text(segment, ""))
        val s = items.indexOfFirst { it is TurnItem.Steps && it.segment == segment }
        return if (s >= 0) items.toMutableList().also { it.add(s, item) } else items + item
    }

    private fun withSteps(items: List<TurnItem>, segment: Int, f: (TurnItem.Steps) -> TurnItem.Steps): List<TurnItem> {
        val i = items.indexOfFirst { it is TurnItem.Steps && it.segment == segment }
        if (i >= 0) return items.toMutableList().also { it[i] = f(items[i] as TurnItem.Steps) }
        return items + f(TurnItem.Steps(segment, emptyList()))
    }

    private fun mapSteps(items: List<TurnItem>, f: (Step) -> Step): List<TurnItem> =
        items.map { if (it is TurnItem.Steps) it.copy(steps = it.steps.map(f)) else it }

    /** Fold one event into a turn's items. Events that are not part of the timeline leave it unchanged. */
    fun apply(items: List<TurnItem>, ev: AgentEvent): List<TurnItem> = when (ev) {
        is AgentEvent.Token -> withText(items, ev.segment) { it.copy(raw = it.raw + ev.text) }
        is AgentEvent.Segment -> if (ev.content.isBlank() && items.none { it is TurnItem.Text && it.segment == ev.segment }) items
            else withText(items, ev.segment) { it.copy(final = ev.content, raw = it.raw.ifEmpty { ev.content }) }
        is AgentEvent.Final -> if (ev.content.isBlank() && items.none { it is TurnItem.Text && it.segment == ev.segment }) items
            else withText(items, ev.segment) { it.copy(final = ev.content, raw = it.raw.ifEmpty { ev.content }) }
        is AgentEvent.StepStarted -> withSteps(items, ev.segment) { s ->
            s.copy(steps = s.steps + Step(ev.id, ev.name, ev.label, icon = ev.icon, email = ev.email))
        }
        is AgentEvent.StepDone -> {
            val known = items.any { it is TurnItem.Steps && it.steps.any { s -> s.id == ev.id } }
            if (known) mapSteps(items) {
                if (it.id == ev.id) it.copy(done = true, ok = ev.ok, summary = ev.summary, count = ev.count, repeatOf = ev.repeatOf,
                    email = it.email ?: ev.email) else it
            }
            else withSteps(items, ev.segment) { s ->
                s.copy(steps = s.steps + Step(ev.id, ev.name, ev.label, true, ev.ok, ev.summary, ev.count, ev.icon, repeatOf = ev.repeatOf,
                    email = ev.email))
            }
        }
        is AgentEvent.ApprovalRequired -> {
            // The last step asked: it waits; the card goes after the steps.
            var marked = false
            val withWait = items.reversed().map { item ->
                if (!marked && item is TurnItem.Steps && item.steps.isNotEmpty()) {
                    marked = true
                    item.copy(steps = item.steps.dropLast(1) + item.steps.last().copy(waiting = true, approvalId = ev.approvalId))
                } else item
            }.reversed()
            if (withWait.any { it is TurnItem.Approval && it.id == ev.approvalId }) withWait
            else withWait + TurnItem.Approval(ev.segment, ev.approvalId)
        }
        is AgentEvent.Session -> if (ev.continuation) items + TurnItem.Resume(ev.segment ?: 0, AppText.get(R.string.asst_after_approval)) else items
        else -> items
    }

    /** The step that asked for approval [id] says what became of it. */
    fun decided(items: List<TurnItem>, id: Long, status: String): List<TurnItem> = mapSteps(items) {
        if (it.approvalId != id) it else it.copy(
            waiting = status == "pending",
            ok = status !in setOf("denied", "failed", "expired"),
            summary = when (status) {
                "approved", "executing", "executed" -> AppText.get(R.string.asst_step_approved)
                "denied" -> AppText.get(R.string.asst_step_denied)
                "expired" -> AppText.get(R.string.asst_step_expired)
                "failed" -> AppText.get(R.string.asst_step_failed)
                else -> it.summary
            },
        )
    }

    /**
     * The people a turn looked up (get_person, done): their cards open from chips under the answer, as
     * on the web (AssistantPanel lookedUp), at most three.
     */
    fun lookedUp(items: List<TurnItem>): List<String> =
        steps(items).filter { it.name == "get_person" && it.done && it.ok }.mapNotNull { it.email?.lowercase() }.distinct().take(3)

    /** Every finalised (or streaming) text, in order: the answer as one text, for Copy. */
    fun text(items: List<TurnItem>): String =
        items.filterIsInstance<TurnItem.Text>().map { it.shown }.filter { it.isNotBlank() }.joinToString("\n\n")

    fun steps(items: List<TurnItem>): List<Step> = items.filterIsInstance<TurnItem.Steps>().flatMap { it.steps }

    /**
     * The timeline as shown: a steps group that only repeats earlier calls joins the group holding
     * them when nothing visible lies between. A question that asked for the same search sixteen
     * times reads as one line, "Searching your tasks ×16". The web
     * does the same (timeline.ts foldRepeatGroups).
     */
    fun foldRepeatGroups(items: List<TurnItem>): List<TurnItem> {
        val out = mutableListOf<TurnItem>()
        for (it in items) {
            if (it is TurnItem.Steps && it.steps.isNotEmpty() && it.steps.all { s -> s.repeatOf != null }) {
                var j = out.lastIndex
                while (j >= 0 && (out[j] as? TurnItem.Text)?.shown?.isBlank() == true) j--
                val prev = out.getOrNull(j) as? TurnItem.Steps
                if (prev != null) {
                    val ids = prev.steps.map { s -> s.id }.toSet()
                    if (it.steps.all { s -> s.repeatOf in ids }) {
                        out[j] = prev.copy(steps = prev.steps + it.steps)
                        continue
                    }
                }
            }
            out += it
        }
        return out
    }

    /** One row per call: a repeat adds to the row of the step it repeats. */
    fun foldRepeats(steps: List<Step>): List<StepRow> {
        val rows = mutableListOf<StepRow>()
        val at = mutableMapOf<String, Int>()
        for (s in steps) {
            val i = s.repeatOf?.let { at[it] }
            if (i != null) {
                rows[i] = rows[i].copy(times = rows[i].times + 1)
                at[s.id] = i
            } else {
                at[s.id] = rows.size
                rows += StepRow(s, 1)
            }
        }
        return rows
    }

    /**
     * The running turn is waiting on the model: no text streaming, no step running, no card
     * waiting. The "Thinking…" row then sits at the end of the timeline.
     */
    fun thinking(items: List<TurnItem>, running: Boolean, pendingApproval: (Long) -> Boolean): Boolean {
        if (!running) return false
        return when (val last = items.lastOrNull()) {
            null -> true
            is TurnItem.Text -> last.final != null || last.raw.isBlank()
            is TurnItem.Steps -> last.steps.none { !it.done || it.waiting }
            is TurnItem.Approval -> !pendingApproval(last.id)
            is TurnItem.Resume -> true
        }
    }
}
