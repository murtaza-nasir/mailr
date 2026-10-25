package io.github.murtazanasir.mailr.data.live

import java.util.concurrent.ConcurrentHashMap

/**
 * One window per item: the editors open now, by item ("draft:<id>"), and the screen that holds
 * each (a composer's back stack entry). Opening an item that is already open brings that screen
 * forward instead of stacking a second copy of it.
 */
object OpenEditors {
    private val open = ConcurrentHashMap<String, String>()

    fun key(kind: String, id: Any): String = "$kind:$id"

    /** [owner] holds the editor of [key] now. */
    fun register(key: String, owner: String) {
        open[key] = owner
    }

    /** [owner] closed (or the item got another key): forget it, unless another owner took the key since. */
    fun unregister(key: String, owner: String) {
        open.remove(key, owner)
    }

    fun owner(key: String): String? = open[key]

    /**
     * The screen to go back to for [key] when it is in [stack] (the open screens, oldest first);
     * null when the item is not open, so a new screen opens.
     */
    fun target(key: String?, stack: List<String>): String? {
        val o = key?.let { open[it] } ?: return null
        return o.takeIf { it in stack }
    }

    internal fun clear() = open.clear()
}
