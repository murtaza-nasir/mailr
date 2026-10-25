package io.github.murtazanasir.mailr.notes

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.notes.R
import io.github.murtazanasir.mailr.i18n.AppText
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.PorterDuff
import android.graphics.PorterDuffColorFilter
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.drawable.IconCompat
import io.github.murtazanasir.mailr.notes.data.DataTable
import io.github.murtazanasir.mailr.notes.data.Note
import io.github.murtazanasir.mailr.notes.data.NotesRepository
import io.github.murtazanasir.mailr.notes.ui.NoteColors
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * "Add to home screen" on a note, list or table, for both apps: a
 * pinned shortcut with the kind's icon on the note's colour. Each app passes the intent that opens
 * the item in itself (the Notes app's `mailr-notes://`, the mailr app's `mailr://notes/…`). The Notes
 * app's recent and Trash-following shortcuts build on this (notesapp/launcher/Shortcuts.kt).
 *
 * One id per item ("note:<id>", "table:<id>"), so a recent shortcut and a pinned one are the same entry.
 */
object HomeShortcuts {
    /** What a shortcut shows: its kind decides the icon, [color] the background. */
    data class Entry(val kind: String, val id: String, val title: String, val list: Boolean, val color: String?)

    fun idOf(kind: String, id: String) = "${if (kind == "table") "table" else "note"}:$id"

    fun entry(n: Note) = Entry("note", n.id, n.title, n.isList, n.color)
    fun entry(t: DataTable) = Entry("table", t.id, t.name, false, t.color)

    private fun label(e: Entry) = e.title.ifBlank { AppText.get(if (e.kind == "table") R.string.notes_untitled_table else if (e.list) R.string.notes_untitled_list else R.string.notes_untitled_note) }

    fun info(context: Context, e: Entry, intent: Intent, rank: Int = 0): ShortcutInfoCompat =
        ShortcutInfoCompat.Builder(context, idOf(e.kind, e.id))
            .setShortLabel(label(e).take(25))
            .setLongLabel(label(e).take(60))
            .setIcon(icon(context, e))
            .setIntent(intent)
            .setRank(rank)
            .build()

    /** The kind's icon (note, list, table) on the note's colour, or on the app's amber. */
    fun icon(context: Context, e: Entry): IconCompat {
        val size = (108 * context.resources.displayMetrics.density).toInt().coerceAtLeast(108)
        val bmp = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val c = Canvas(bmp)
        c.drawColor(NoteColors.argb(e.color) ?: 0xFFFBBC04.toInt())
        val res = when {
            e.kind == "table" -> R.drawable.ic_table
            e.list -> R.drawable.ic_list
            else -> R.drawable.ic_note
        }
        ContextCompat.getDrawable(context, res)?.mutate()?.let { d ->
            val inset = size * 33 / 100
            d.setBounds(inset, inset, size - inset, size - inset)
            d.colorFilter = PorterDuffColorFilter(0xFF3C4043.toInt(), PorterDuff.Mode.SRC_IN)
            d.draw(c)
        }
        return IconCompat.createWithAdaptiveBitmap(bmp)
    }

    /** Ask the launcher to pin a shortcut; false when it cannot. */
    fun pin(context: Context, e: Entry, intent: Intent): Boolean {
        if (!ShortcutManagerCompat.isRequestPinShortcutSupported(context)) return false
        return catching { ShortcutManagerCompat.requestPinShortcut(context, info(context, e, intent), null) }.getOrDefault(false)
    }

    /**
     * The ⋮ entry's work: the item from the phone's copy, pinned with [intent]'s target, or a message
     * when it is not on the server yet or the launcher cannot pin.
     */
    fun pinToHome(context: Context, repo: NotesRepository, scope: CoroutineScope, kind: String, id: String,
                  intent: (kind: String, id: String) -> Intent) {
        scope.launch {
            val e = if (kind == "table") repo.tableNow(id)?.let { entry(it) } else repo.noteNow(id)?.let { entry(it) }
            val ok = e != null && !id.startsWith("local-") && pin(context, e, intent(e.kind, e.id))
            if (!ok) withContext(Dispatchers.Main) {
                Toast.makeText(context, AppText.get(if (id.startsWith("local-")) R.string.notes_shortcut_not_saved else R.string.notes_shortcut_unsupported),
                    Toast.LENGTH_SHORT).show()
            }
        }
    }
}

/** An export handed to another app through the share sheet (text, with the file name as its subject). */
fun shareText(ctx: Context, name: String, text: String) {
    val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
        .putExtra(Intent.EXTRA_SUBJECT, name).putExtra(Intent.EXTRA_TITLE, name)
    catching { ctx.startActivity(Intent.createChooser(send, name).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
}
