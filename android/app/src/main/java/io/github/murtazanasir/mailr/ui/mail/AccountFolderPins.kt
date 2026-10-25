package io.github.murtazanasir.mailr.ui.mail

import io.github.murtazanasir.mailr.data.api.Label
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.longOrNull

/**
 * Account groups stay short (the web's lib/accountFolders.ts, backend
 * core/accountpins.py). With "Show each account separately" on, an account's group in the drawer
 * shows its system folders, the folders pinned to that account in the user's order, and one row
 * "More folders" with the unread mail of the folders inside it, which opens to the rest. Three
 * settings hold it, the same on the web: `account_folder_pins` ({"<account id>": [label id, …]}),
 * `account_folders_more_open` ([account id, …]) and `account_folders_show_unread`.
 */
data class AccountFolderPrefs(
    val pins: Map<Long, List<Long>> = emptyMap(),
    val moreOpen: Set<Long> = emptySet(),
    val showUnread: Boolean = false,
) {
    fun pinsOf(accountId: Long): List<Long> = pins[accountId].orEmpty()
}

/** The pins, the open More folders and the option, from the settings (anything malformed is left out). */
fun accountFolderPrefs(settings: JsonObject?): AccountFolderPrefs {
    val pins = (settings?.get("account_folder_pins") as? JsonObject)?.mapNotNull { (k, v) ->
        val acct = k.toLongOrNull() ?: return@mapNotNull null
        acct to ((v as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.longOrNull } ?: return@mapNotNull null)
    }?.toMap().orEmpty()
    val open = (settings?.get("account_folders_more_open") as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.longOrNull }?.toSet().orEmpty()
    val unread = (settings?.get("account_folders_show_unread") as? JsonPrimitive)?.booleanOrNull ?: false
    return AccountFolderPrefs(pins, open, unread)
}

/** The setting's value for [pins]. */
fun pinsJson(pins: Map<Long, List<Long>>): JsonObject =
    JsonObject(pins.filterValues { it.isNotEmpty() }.entries.associate { (k, v) -> k.toString() to JsonArray(v.map(::JsonPrimitive)) })

fun openJson(open: Set<Long>): JsonArray = JsonArray(open.map(::JsonPrimitive))

/** [labelId] pinned to the account, before [before] (a pinned id), else at the end. Pinning a pinned one moves it. */
fun pinFolder(pins: Map<Long, List<Long>>, accountId: Long, labelId: Long, before: Long? = null): Map<Long, List<Long>> {
    val list = pins[accountId].orEmpty().filter { it != labelId }.toMutableList()
    val at = before?.let { list.indexOf(it) } ?: -1
    if (at < 0) list += labelId else list.add(at, labelId)
    return pins + (accountId to list)
}

/** The pins without [labelId] in the account (an emptied account goes away). */
fun unpinFolder(pins: Map<Long, List<Long>>, accountId: Long, labelId: Long): Map<Long, List<Long>> {
    val list = pins[accountId].orEmpty().filter { it != labelId }
    return if (list.isEmpty()) pins - accountId else pins + (accountId to list)
}

/** One step up (-1) or down (1) among the pinned folders shown ([among]); unchanged at either end. */
fun movePin(pins: Map<Long, List<Long>>, accountId: Long, labelId: Long, delta: Int, among: List<Long>? = null): Map<Long, List<Long>> {
    val all = pins[accountId].orEmpty()
    val order = among?.let { a -> all.filter { it in a } } ?: all
    val i = order.indexOf(labelId)
    val j = i + delta
    if (i < 0 || j < 0 || j >= order.size) return pins
    val next = order.toMutableList().apply { removeAt(i); add(j, labelId) }
    return pins + (accountId to next + all.filter { it !in order })
}

/**
 * An account's folders split as its group shows them: [pinned] in the user's order, [surfaced]
 * (unread while the option is on, and the open folder while More folders is closed), [rest] under
 * More folders, and [restUnread], the unread sum of the folders inside it (hidden labels left out).
 */
data class FolderSplit(val pinned: List<Label>, val surfaced: List<Label>, val rest: List<Label>, val restUnread: Int)

fun splitAccountFolders(
    folders: List<Label>, labels: List<Label>, pinIds: List<Long>, unread: (Label) -> Int,
    showUnread: Boolean, moreOpen: Boolean, current: Long? = null,
): FolderSplit {
    val byId = labels.filter { it.role == null }.associateBy { it.id }
    val pinned = pinIds.mapNotNull { byId[it] }
    val pinnedIds = pinned.map { it.id }.toSet()
    val surfaced = mutableListOf<Label>()
    val rest = mutableListOf<Label>()
    for (l in folders.filter { it.role == null && it.id !in pinnedIds }.sortedBy { it.name.lowercase() }) {
        val lit = showUnread && l.showInList != "hide" && unread(l) > 0
        if (lit || (!moreOpen && current == l.id)) surfaced += l else rest += l
    }
    return FolderSplit(pinned, surfaced, rest, rest.sumOf { if (it.showInList == "hide") 0 else unread(it) })
}

/** The label id an account view opens (`acct:<id>:label:<label id>`) when it is [accountId]'s, else null. */
fun accountLabelOf(view: String, accountId: Long): Long? {
    val prefix = "acct:$accountId:label:"
    return if (view.startsWith(prefix)) view.removePrefix(prefix).toLongOrNull() else null
}
