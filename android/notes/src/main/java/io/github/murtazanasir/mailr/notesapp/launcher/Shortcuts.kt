package io.github.murtazanasir.mailr.notesapp.launcher

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.notesapp.R
import io.github.murtazanasir.mailr.i18n.AppText
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import io.github.murtazanasir.mailr.notes.HomeShortcuts
import io.github.murtazanasir.mailr.notes.data.DataTable
import io.github.murtazanasir.mailr.notes.data.Note
import io.github.murtazanasir.mailr.notesapp.DeepLinks
import io.github.murtazanasir.mailr.notesapp.MainActivity

/**
 * Launcher shortcuts (surface `android.noteslauncher`):
 *
 * - static New note and New list (res/xml/shortcuts.xml);
 * - dynamic: the last three notes, lists or tables opened ([opened]);
 * - pinned: "Add to home screen" on any note, list or table ([pin]), with the kind's icon on the
 *   note's colour (notes-core's HomeShortcuts draws it; the mailr app pins the same way). A pinned
 *   shortcut to an item in Trash is disabled with "This note is in Trash"; restoring enables it
 *   again ([follow]).
 *
 * One id per item ("note:<id>", "table:<id>"), so a recent shortcut and a pinned one are the same entry.
 */
object Shortcuts {
    const val RECENT = 3
    val TRASHED: String get() = AppText.get(R.string.notesapp_note_in_trash)
    val TRASHED_TABLE: String get() = AppText.get(R.string.notesapp_table_in_trash)
    val GONE: String get() = AppText.get(R.string.notesapp_shortcut_gone)
    val SIGNED_OUT: String get() = AppText.get(R.string.notesapp_shortcut_signed_out)

    fun idOf(kind: String, id: String) = HomeShortcuts.idOf(kind, id)

    fun intent(context: Context, kind: String, id: String): Intent =
        Intent(Intent.ACTION_VIEW, Uri.parse(if (kind == "table") DeepLinks.tableUri(id) else DeepLinks.noteUri(id)),
            context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)

    fun entry(n: Note) = HomeShortcuts.entry(n)
    fun entry(t: DataTable) = HomeShortcuts.entry(t)

    fun info(context: Context, e: HomeShortcuts.Entry, rank: Int = 0): ShortcutInfoCompat =
        HomeShortcuts.info(context, e, intent(context, e.kind, e.id), rank)

    /** A note or table was opened: it becomes the first of the dynamic shortcuts (the last three opened). */
    fun opened(context: Context, recents: List<HomeShortcuts.Entry>) {
        val list = recents.take(RECENT).mapIndexed { i, e -> info(context, e, i) }
        catching { ShortcutManagerCompat.setDynamicShortcuts(context, list) }
    }

    /** ACT note.add_to_home @android.noteslauncher: ask the launcher to pin a shortcut; false when it cannot. */
    fun pin(context: Context, e: HomeShortcuts.Entry): Boolean = HomeShortcuts.pin(context, e, intent(context, e.kind, e.id))

    /** What [follow] does: shortcut ids to disable, each with its message, and entries to enable or update. */
    data class FollowPlan(val disable: Map<String, String>, val enable: List<Pair<HomeShortcuts.Entry, Int>>)

    /**
     * Keep pinned and recent shortcuts in step with the phone's copy: an item in Trash is disabled
     * with "This note is in Trash", an item gone for good with "no longer available", a restored
     * item enabled again with its current title and colour. [shortcuts]: (id, rank) of the
     * launcher's pinned and dynamic shortcuts.
     */
    fun plan(shortcuts: List<Pair<String, Int>>, notes: List<Note>, tables: List<DataTable>): FollowPlan {
        val byId = notes.associateBy { idOf("note", it.id) }
        val tablesById = tables.associateBy { idOf("table", it.id) }
        val disable = LinkedHashMap<String, String>()
        val enable = ArrayList<Pair<HomeShortcuts.Entry, Int>>()
        for ((id, rank) in shortcuts) {
            val n = byId[id]
            val t = tablesById[id]
            when {
                n != null && n.deletedAt != null -> disable[id] = TRASHED
                t != null && t.deletedAt != null -> disable[id] = TRASHED_TABLE
                n != null -> enable += entry(n) to rank
                t != null -> enable += entry(t) to rank
                id.startsWith("note:") || id.startsWith("table:") -> disable[id] = GONE
            }
        }
        return FollowPlan(disable, enable)
    }

    fun follow(context: Context, notes: List<Note>, tables: List<DataTable>) {
        val sm = context.getSystemService(android.content.pm.ShortcutManager::class.java)
        val all = catching { (sm.pinnedShortcuts + sm.dynamicShortcuts).distinctBy { it.id } }.getOrDefault(emptyList())
        val p = plan(all.map { it.id to it.rank.coerceAtLeast(0) }, notes, tables)
        catching {
            p.disable.entries.groupBy({ it.value }, { it.key }).forEach { (message, ids) ->
                ShortcutManagerCompat.disableShortcuts(context, ids, message)
            }
            if (p.enable.isNotEmpty()) {
                val infos = p.enable.map { (e, rank) -> info(context, e, rank) }
                ShortcutManagerCompat.enableShortcuts(context, infos)
                ShortcutManagerCompat.updateShortcuts(context, infos)
            }
        }
    }

    /** Sign-out: no recent shortcuts; pinned ones wait, disabled, for the next sign-in. */
    fun signedOut(context: Context) {
        catching {
            ShortcutManagerCompat.removeAllDynamicShortcuts(context)
            val pinned = context.getSystemService(android.content.pm.ShortcutManager::class.java).pinnedShortcuts.map { it.id }
            if (pinned.isNotEmpty()) ShortcutManagerCompat.disableShortcuts(context, pinned, SIGNED_OUT)
        }
    }
}
