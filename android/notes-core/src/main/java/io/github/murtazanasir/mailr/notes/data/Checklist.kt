package io.github.murtazanasir.mailr.notes.data

import java.security.SecureRandom

/** Ordering and small helpers shared by the repository and the screens (display order). */
object Checklist {
    /** One group of the editor: the top group (section null) or a section, with its unchecked items in display order. */
    data class Group(val section: Section?, val items: List<Item>, val mode: String, val collapsed: Boolean)

    /**
     * [checked] is what the fold lists; [checkedCount] is its "n checked" and also counts the checked
     * items that stay in their groups ([layout]'s `stay`).
     */
    data class Layout(val groups: List<Group>, val checked: List<Item>, val checkedCount: Int = checked.size)

    /**
     * The display order: the top group, then the sections by `order`; inside a group in `manual` mode
     * by `order`, in `learned` mode items with a rank by rank (ties by `order`), then items without
     * one by `added_at`. With [checkedAtBottom] checked items leave their group for the fold, newest
     * check first; without it they keep their place. A checked item whose id is in [stay] keeps its
     * place too: the editor passes the items it has shown unchecked since the note was opened, so a
     * check does not move the row until the note is opened again. Only the display changes.
     */
    fun layout(items: List<Item>, sections: List<Section>, top: TopGroup, checkedAtBottom: Boolean,
               stay: Set<String> = emptySet()): Layout {
        val secs = sections.sortedWith(compareBy({ it.order }, { it.name }))
        val known = secs.map { it.id }.toSet()
        fun inGroup(sid: String?) = items.filter { (it.sectionId?.takeIf { s -> s in known }) == sid }
        fun arrange(list: List<Item>, mode: String): List<Item> = if (mode == "learned") {
            val (ranked, rest) = list.partition { it.rank != null }
            ranked.sortedWith(compareBy<Item>({ it.rank }, { it.order })) +
                rest.sortedWith(compareBy<Item>({ it.addedAt ?: "￿" }, { it.order }))
        } else list.sortedWith(compareBy({ it.order }, { it.addedAt ?: "" }))
        fun shown(g: List<Item>) = if (checkedAtBottom) g.filter { !it.checked || it.id in stay } else g
        val groups = ArrayList<Group>()
        groups += Group(null, arrange(shown(inGroup(null)), top.orderMode), top.orderMode, top.collapsed)
        for (s in secs) groups += Group(s, arrange(shown(inGroup(s.id)), s.orderMode), s.orderMode, s.collapsed)
        if (!checkedAtBottom) return Layout(groups, emptyList())
        val allChecked = items.filter { it.checked }
        val checked = allChecked.filter { it.id !in stay }.sortedByDescending { it.checkedAt ?: "" }
        return Layout(groups, checked, allChecked.size)
    }

    /** A key that sorts after [a] and before [b] (either may be null), for an optimistic move; the server's own keys replace it. */
    fun between(a: String?, b: String?): String {
        val lo = a ?: ""
        if (b == null || b <= lo) return lo + "V"
        val sb = StringBuilder()
        var i = 0
        while (true) {
            // Past the end of `lo`, a character below every key character stands in ('/').
            val ca = if (i < lo.length) lo[i].code else 0x2F
            val cb = b[i].code
            if (ca == cb) {
                sb.append(ca.toChar()); i++; continue
            }
            if (cb - ca > 1) return sb.append(((ca + cb) / 2).toChar()).toString()
            sb.append(ca.toChar())
            return sb.toString() + (if (i + 1 <= lo.length) lo.substring(i + 1) else "") + "V"
        }
    }

    private val random = SecureRandom()

    /** A UUID v7 (time-ordered): items and rows get their ids on the phone, so an offline add replays once. */
    fun uuid7(now: Long = System.currentTimeMillis()): String {  // clock-ok: id generation
        val b = ByteArray(16)
        random.nextBytes(b)
        for (i in 0 until 6) b[i] = (now ushr (8 * (5 - i))).toByte()
        b[6] = ((b[6].toInt() and 0x0f) or 0x70).toByte()
        b[8] = ((b[8].toInt() and 0x3f) or 0x80).toByte()
        val hex = b.joinToString("") { "%02x".format(it) }
        return "${hex.substring(0, 8)}-${hex.substring(8, 12)}-${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}"
    }

    /** A temporary id for something the server names (a note, a section, a table) made offline. */
    fun localId(prefix: String) = "local-$prefix-" + uuid7().replace("-", "").takeLast(12)

    fun isLocal(id: String?) = id != null && id.startsWith("local-")
}
