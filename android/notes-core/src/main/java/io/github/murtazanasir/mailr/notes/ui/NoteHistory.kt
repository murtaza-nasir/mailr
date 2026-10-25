package io.github.murtazanasir.mailr.notes.ui

import io.github.murtazanasir.mailr.util.catching

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import io.github.murtazanasir.mailr.notes.data.NotesRepository
import io.github.murtazanasir.mailr.notes.data.notesMessage
import io.github.murtazanasir.mailr.notes.edit.EditHistory
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.Json

/**
 * Undo and redo in the note editor, for one open note.
 * The model is [EditHistory]; this applies its steps.
 *
 * - A text step (the title, the body) sets the field through the editor's [writer]; the editor then
 *   autosaves it like any edit (a new version, the live editing merge, the queue when offline).
 * - An operation (a tick, an added or deleted item, a move, a section, a colour, labels, text <-> list)
 *   keeps the repository's [NotesRepository.UndoHandle] and the action itself. Undo runs the handle:
 *   a write still queued is taken out, a write the server has gets the server's undo, queued like
 *   any write, so it works offline. Redo runs the action again and keeps its new handle.
 * - Steps run one after another, so quick presses keep their order.
 */
class NoteHistory(val noteId: String, private val clock: () -> Long) {

    class EditOp(var handle: NotesRepository.UndoHandle?, val redo: suspend () -> NotesRepository.UndoHandle?)

    val history = EditHistory<EditOp>()
    private val lock = Mutex()

    /** Bumped on every change, so the editor's buttons recompose. */
    var version by mutableIntStateOf(0)
        private set

    val canUndo get() = version >= 0 && history.canUndo
    val canRedo get() = version >= 0 && history.canRedo

    /**
     * The editor's text fields while it shows the note: read a field, set a field (it autosaves). The
     * writer also gets where the caret goes: the end of the text that came back or went in, so the
     * cursor stands where the change was (EditHistory.caretAfter, as on the web).
     */
    var reader: ((String) -> String?)? = null
    var writer: ((field: String, value: String, caret: Int) -> Unit)? = null

    private fun bump() {
        version++
    }

    fun recordText(field: String, before: String, after: String, inputType: String? = null): EditHistory.Recorded<EditOp>? =
        history.recordText(field, before, after, clock(), inputType)?.also { bump() }

    fun recordOp(label: String, handle: NotesRepository.UndoHandle, redo: suspend () -> NotesRepository.UndoHandle?): EditHistory.OpStep<EditOp> =
        history.recordOp(label, EditOp(handle, redo)).also { bump() }

    fun seal() = history.seal()

    suspend fun undo(say: (String) -> Unit) = move(true, null, say)
    suspend fun redo(say: (String) -> Unit) = move(false, null, say)

    /** The snackbar's Undo for [step]: the newest step goes back as Undo does; an older one is reversed where it is. */
    suspend fun undoStep(step: EditHistory.Step<EditOp>, say: (String) -> Unit) = move(true, step, say)

    private suspend fun move(back: Boolean, only: EditHistory.Step<EditOp>?, say: (String) -> Unit) = lock.withLock {
        if (only != null && history.peekUndo() !== only) {
            when (only) {
                is EditHistory.TextStep -> {
                    val cur = reader?.invoke(only.field) ?: return@withLock
                    val next = EditHistory.revertText(cur, only.before, only.after) ?: return@withLock
                    history.recordText(only.field, cur, next, clock(), "historyUndo")
                    writer?.invoke(only.field, next, EditHistory.caretAfter(cur, next))
                }
                is EditHistory.OpStep -> {
                    runOp(only, true, say)
                    history.drop(only)
                }
            }
            bump()
            return@withLock
        }
        val s = (if (back) history.undo() else history.redo()) ?: return@withLock
        bump()
        val ok = when (s) {
            is EditHistory.TextStep -> applyText(s, back, say)
            is EditHistory.OpStep -> runOp(s, back, say)
        }
        if (!ok) history.drop(s)
        bump()
    }

    private fun applyText(s: EditHistory.TextStep, back: Boolean, say: (String) -> Unit): Boolean {
        val cur = reader?.invoke(s.field) ?: return false
        val next = if (back) EditHistory.revertText(cur, s.before, s.after) else EditHistory.reapplyText(cur, s.before, s.after)
        if (next == null) {
            say(io.github.murtazanasir.mailr.i18n.AppText.get(io.github.murtazanasir.mailr.notes.R.string.notes_text_changed_elsewhere))
            return false
        }
        writer?.invoke(s.field, next, EditHistory.caretAfter(cur, next)) ?: return false
        return true
    }

    private suspend fun runOp(s: EditHistory.OpStep<EditOp>, back: Boolean, say: (String) -> Unit): Boolean = try {
        if (back) {
            val h = s.op.handle
            if (h == null) false else {
                h.undo()
                s.op.handle = null
                true
            }
        } else {
            s.op.handle = s.op.redo()
            s.op.handle != null
        }
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        say(e.notesMessage())
        false
    }

    /** The text steps as a string for the saved state (after the process went); the newest that fit [budget]. */
    fun saved(budget: Int = 64_000): String? {
        val all = history.save()
        if (all.done.isEmpty() && all.undone.isEmpty()) return null
        var done = all.done
        var text = Json.encodeToString(EditHistory.Saved.serializer(), all.copy(done = done))
        while (text.length > budget && done.isNotEmpty()) {
            done = done.drop(1)
            text = Json.encodeToString(EditHistory.Saved.serializer(), EditHistory.Saved(done, all.undone.takeIf { done.isNotEmpty() }.orEmpty()))
        }
        return text.takeIf { it.length <= budget }
    }

    fun restore(saved: String) {
        catching { Json.decodeFromString(EditHistory.Saved.serializer(), saved) }.getOrNull()?.let {
            history.restore(it)
            bump()
        }
    }
}
