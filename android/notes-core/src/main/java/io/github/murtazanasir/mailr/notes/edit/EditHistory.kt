package io.github.murtazanasir.mailr.notes.edit

import kotlinx.serialization.Serializable

/**
 * The editor's undo and redo history, the twin of the
 * web's frontend/src/lib/editHistory.ts. Both run spec/cases/edit-history-cases.yaml.
 *
 * A step is a text edit of one field (the title, the body) or an operation (a tick, a deleted item, a
 * colour). Typing joins the step before it while the same kind of typing goes on in the same place
 * within [IDLE_MS] and no word ends; a paste, a cut, a replacement (autocorrect, replace-all, an
 * Ambrose rewrite, dictation) is one step of its own. A new step after an undo drops the redo tail.
 * The history keeps [CAP] steps; the oldest go first. Undo and redo move a step between the two
 * stacks; the editor applies it.
 */
class EditHistory<P>(val cap: Int = CAP, val idleMs: Long = IDLE_MS) {

    enum class Group { INSERT, DELETE, ONE }

    sealed interface Step<out P>

    @Serializable
    data class TextStep(
        val field: String,
        val before: String,
        var after: String,
        val at: Long,
        var last: Long,
        val group: Group,
        /** Typing may still join it. */
        var open: Boolean,
    ) : Step<Nothing>

    class OpStep<P>(val label: String, val op: P) : Step<P>

    data class Recorded<P>(val step: Step<P>, val merged: Boolean, val big: Boolean)

    data class Change(val at: Int, val removed: String, val inserted: String)

    private val done = ArrayList<Step<P>>()
    private val undone = ArrayList<Step<P>>()

    val canUndo get() = done.isNotEmpty()
    val canRedo get() = undone.isNotEmpty()
    val undoSize get() = done.size
    val redoSize get() = undone.size
    fun peekUndo(): Step<P>? = done.lastOrNull()
    fun peekRedo(): Step<P>? = undone.lastOrNull()
    fun steps(): List<Step<P>> = done.toList()

    /** A field changed from [before] to [after] at [at] ms; [inputType] as the browser names it, when known. */
    fun recordText(field: String, before: String, after: String, at: Long, inputType: String? = null): Recorded<P>? {
        if (before == after) return null
        val c = diff(before, after)
        val group = classify(c, inputType)
        val big = group == Group.ONE && isBigRemoval(before, after)
        val top = peekUndo()
        if (group != Group.ONE && top is TextStep && top.open && top.field == field && top.group == group &&
            top.after == before && at - top.last <= idleMs && continues(top, c, group)) {
            top.after = after
            top.last = at
            undone.clear()
            return Recorded(top, merged = true, big = false)
        }
        seal()
        val step = TextStep(field, before, after, at, at, group, group != Group.ONE)
        push(step)
        return Recorded(step, merged = false, big = big)
    }

    /** An operation: always a step of its own. */
    fun recordOp(label: String, op: P): OpStep<P> {
        seal()
        val step = OpStep(label, op)
        push(step)
        return step
    }

    /** End the typing group (the field lost focus, an undo ran). */
    fun seal() {
        (peekUndo() as? TextStep)?.open = false
    }

    fun undo(): Step<P>? {
        seal()
        val s = done.removeLastOrNull() ?: return null
        undone += s
        return s
    }

    fun redo(): Step<P>? {
        val s = undone.removeLastOrNull() ?: return null
        (s as? TextStep)?.open = false
        done += s
        return s
    }

    /** A step that could not be applied leaves both stacks. */
    fun drop(step: Step<P>) {
        done.removeAll { it === step }
        undone.removeAll { it === step }
    }

    fun clear() {
        done.clear()
        undone.clear()
    }

    /** The text steps, for a restore after the process went (an operation stays with the process). */
    @Serializable
    data class Saved(val done: List<TextStep>, val undone: List<TextStep>)

    fun save(): Saved = Saved(done.filterIsInstance<TextStep>().map { it.copy(open = false) },
        undone.filterIsInstance<TextStep>().map { it.copy(open = false) })

    fun restore(s: Saved) {
        clear()
        done += s.done
        undone += s.undone
    }

    private fun continues(top: TextStep, c: Change, group: Group): Boolean {
        val typed = diff(top.before, top.after)
        if (group == Group.INSERT) {
            if (c.at != typed.at + typed.inserted.length) return false
            val prev = lastChar(typed.inserted)
            return !(isSpace(prev) && !isSpace(c.inserted))
        }
        val end = typed.at
        if (c.at != end - c.removed.length && c.at != end) return false
        if (c.at == end - c.removed.length) {
            val prev = typed.removed.take(1)
            return !(isSpace(c.removed) && prev.isNotEmpty() && !isSpace(prev))
        }
        return true
    }

    private fun push(step: Step<P>) {
        undone.clear()
        done += step
        while (done.size > cap) done.removeAt(0)
    }

    companion object {
        const val IDLE_MS = 1000L
        const val CAP = 200

        /** Input types that are always one step of their own (the browser's names; Android passes its own). */
        private val ONE_STEP = setOf(
            "insertFromPaste", "insertFromPasteAsQuotation", "insertFromDrop", "insertFromYank", "insertReplacementText",
            "insertTranspose", "deleteByCut", "deleteByDrag", "historyUndo", "historyRedo",
        )

        fun diff(before: String, after: String): Change {
            var p = 0
            val max = minOf(before.length, after.length)
            while (p < max && before[p] == after[p]) p++
            var s = 0
            while (s < max - p && before[before.length - 1 - s] == after[after.length - 1 - s]) s++
            if (p > 0 && before[p - 1].isHighSurrogate()) p--
            return Change(p, before.substring(p, before.length - s), after.substring(p, after.length - s))
        }

        private fun chars(s: String) = s.codePointCount(0, s.length)
        private fun isSpace(s: String) = s.isNotEmpty() && chars(s) == 1 && (Character.isWhitespace(s.codePointAt(0)) || Character.isSpaceChar(s.codePointAt(0)))
        private fun lastChar(s: String): String {
            if (s.isEmpty()) return ""
            val i = s.offsetByCodePoints(s.length, -1)
            return s.substring(i)
        }

        fun classify(c: Change, inputType: String? = null): Group {
            if (inputType != null && inputType in ONE_STEP) return Group.ONE
            if (c.removed.isEmpty() && chars(c.inserted) == 1 && c.inserted != "\n") return Group.INSERT
            if (c.inserted.isEmpty() && chars(c.removed) == 1) return Group.DELETE
            return Group.ONE
        }

        /** Lines a removal takes away (a line break at either end of it does not count as a line). */
        fun linesRemoved(c: Change): Int =
            if (c.removed.isEmpty()) 0 else c.removed.removePrefix("\n").removeSuffix("\n").split('\n').size

        /** More than three lines, or a field cleared of 20 characters or more, in one step. */
        fun isBigRemoval(before: String, after: String): Boolean {
            val c = diff(before, after)
            if (c.removed.isEmpty()) return false
            if (linesRemoved(c) > 3) return true
            return after.isBlank() && c.removed.trim().length >= 20
        }

        /** Undo a text step on the field's current value (null: the text changed too much). */
        fun revertText(current: String, before: String, after: String): String? = rebase(current, after, before)

        /** Redo a text step on the current value. */
        fun reapplyText(current: String, before: String, after: String): String? = rebase(current, before, after)

        private fun rebase(current: String, from: String, to: String): String? {
            if (current == from) return to
            val c = diff(from, to)
            val head = from.substring(maxOf(0, c.at - 20), c.at)
            val tailStart = c.at + c.removed.length
            val tail = from.substring(tailStart, minOf(from.length, tailStart + 20))
            val needle = head + c.removed + tail
            if (needle.isEmpty()) return null
            val i = current.indexOf(needle)
            if (i < 0 || current.indexOf(needle, i + 1) >= 0) return null
            val at = i + head.length
            return current.substring(0, at) + c.inserted + current.substring(at + c.removed.length)
        }

        /** Where the caret goes after a text step is applied: the end of what came back. */
        fun caretAfter(from: String, to: String): Int {
            val c = diff(from, to)
            return c.at + c.inserted.length
        }
    }
}
