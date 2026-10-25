package io.github.murtazanasir.mailr.ui.calendar

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.notes.edit.EditHistory
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Undo and redo in the task editor, for one
 * open task. The model is the notes editor's [EditHistory] (the same steps, the same shared vectors);
 * this applies its steps to the editor.
 *
 * - A text step (the title, the notes) sets the field through the editor's [writer], with the cursor
 *   where the change was; the editor then autosaves it like typing.
 * - A field step (the due, the start, the priority, the list, tags, reminders, the repeat) holds the
 *   editor's fields before and after ([TaskEditorState.opFields]). Undo puts back each field that still
 *   has the value the step gave it, so a change someone else made since stays.
 * - A subtask step (one added, one ticked) holds its undo and its redo, which write at once.
 * - Steps run one after another, so quick presses keep their order.
 */
class TaskHistory(private val clock: () -> Long) {

    sealed interface Op
    class Fields(val before: List<Any?>, val after: List<Any?>) : Op
    class Action(val undo: suspend () -> Boolean, val redo: suspend () -> Boolean) : Op

    val history = EditHistory<Op>()
    private val lock = Mutex()

    /** Bumped on every change, so the top bar's buttons recompose. */
    var version by mutableIntStateOf(0)
        private set

    val canUndo get() = version >= 0 && history.canUndo
    val canRedo get() = version >= 0 && history.canRedo

    /** The editor's text fields ("title", "notes") while it shows the task; [writer] also gets the caret. */
    var reader: ((String) -> String?)? = null
    var writer: ((field: String, value: String, caret: Int) -> Unit)? = null

    /** The editor's other fields: read them all, or set one by its index. */
    var fieldsReader: (() -> List<Any?>)? = null
    var fieldWriter: ((Int, Any?) -> Unit)? = null

    private fun bump() {
        version++
    }

    fun recordText(field: String, before: String, after: String, inputType: String? = null): EditHistory.Recorded<Op>? =
        history.recordText(field, before, after, clock(), inputType)?.also { bump() }

    /** The fields went from [before] to [after]: one step, or nothing when nothing changed. */
    fun recordFields(label: String, before: List<Any?>, after: List<Any?>): EditHistory.OpStep<Op>? =
        if (before == after) null else history.recordOp(label, Fields(before, after)).also { bump() }

    fun recordAction(label: String, undo: suspend () -> Boolean, redo: suspend () -> Boolean): EditHistory.OpStep<Op> =
        history.recordOp(label, Action(undo, redo)).also { bump() }

    fun seal() = history.seal()

    suspend fun undo(say: (String) -> Unit = {}) = move(true, say)
    suspend fun redo(say: (String) -> Unit = {}) = move(false, say)

    private suspend fun move(back: Boolean, say: (String) -> Unit) = lock.withLock {
        val s = (if (back) history.undo() else history.redo()) ?: return@withLock
        bump()
        val ok = when (s) {
            is EditHistory.TextStep -> applyText(s, back)
            is EditHistory.OpStep -> when (val op = s.op) {
                is Fields -> applyFields(op, back)
                is Action -> runAction(op, back, say)
            }
        }
        if (!ok) {
            history.drop(s)
            if (s !is EditHistory.OpStep || s.op !is Action) say(AppText.get(R.string.cal_task_changed_elsewhere))
        }
        bump()
    }

    private fun applyText(s: EditHistory.TextStep, back: Boolean): Boolean {
        val cur = reader?.invoke(s.field) ?: return false
        val next = (if (back) EditHistory.revertText(cur, s.before, s.after) else EditHistory.reapplyText(cur, s.before, s.after))
            ?: return false
        writer?.invoke(s.field, next, EditHistory.caretAfter(cur, next)) ?: return false
        return true
    }

    private fun applyFields(op: Fields, back: Boolean): Boolean {
        val cur = fieldsReader?.invoke() ?: return false
        val set = fieldWriter ?: return false
        val from = if (back) op.after else op.before
        val to = if (back) op.before else op.after
        var any = false
        for (i in to.indices) {
            if (from[i] == to[i] || cur.getOrNull(i) != from[i]) continue
            set(i, to[i])
            any = true
        }
        return any
    }

    private suspend fun runAction(op: Action, back: Boolean, say: (String) -> Unit): Boolean = try {
        if (back) op.undo() else op.redo()
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        say(e.userMessage())
        false
    }
}
