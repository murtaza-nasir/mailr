package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.ConnectorSyncStatus
import io.github.murtazanasir.mailr.i18n.AppText

/**
 * One group of the drawer's calendars: the Mailr calendar, a connected account, the calendars
 * shared with the user, one phone's calendars or the calendar links ([kind]: builtin, account,
 * shared, phone, link; "connector" for a server that does not say where a calendar comes from).
 * [sub] is the account's host or the phone's name. [error] is the technical text of a sync
 * failure, [errorKind] and [errorHost] what [SyncProblems] explains it with; [authFailed] when the
 * server refused the password.
 */
data class AccountGroup(
    val id: Long?, val name: String, val error: String?, val collections: List<CalendarCollection>,
    val authFailed: Boolean = false, val kind: String = "connector", val sub: String? = null,
    val errorKind: String? = null, val errorHost: String? = null,
) {
    /** A new calendar can be made here: the Mailr calendar or a connected account. */
    val canCreate: Boolean get() = kind == "builtin" || kind == "account" || kind == "connector"

    /** The heading in one line: "Studio · cal.example.org". */
    val heading: String get() = if (sub.isNullOrBlank()) name else "$name · $sub"
}

/** The drawer's groups, built from the calendars' `source` (the server's mirror.source_of). */
object CalendarGroups {
    private val ORDER = listOf("builtin", "account", "connector", "shared", "phone", "link")

    /**
     * [cols] grouped in this order: the Mailr calendar, the connected accounts, shared with you,
     * phones, links; inside a group the calendars keep their order. [accounts] (GET
     * /calendar/sync-status) gives the accounts' names and sync failures. A calendar without a
     * source (an older server) is grouped by its account, as before.
     */
    fun group(cols: List<CalendarCollection>, accounts: List<ConnectorSyncStatus>): List<AccountGroup> {
        val byId = accounts.associateBy { it.id }
        val legacyAccounts = cols.filter { it.source == null }.map { it.connectorId }.distinct().size
        val groups = LinkedHashMap<String, MutableList<CalendarCollection>>()
        for (c in cols) groups.getOrPut(keyOf(c)) { mutableListOf() } += c
        return groups.entries.mapIndexed { i, (key, cs) -> i to build(key, cs, byId, legacyAccounts > 1) }
            .sortedWith(compareBy({ ORDER.indexOf(it.second.kind) }, { it.first }))
            .map { it.second }
    }

    private fun keyOf(c: CalendarCollection): String {
        val s = c.source ?: return "connector:${c.connectorId}"
        return when (s.kind) {
            "account" -> "account:${c.connectorId}"
            "phone" -> "phone:${s.device.orEmpty()}"
            "builtin", "shared", "link" -> s.kind
            else -> "connector:${c.connectorId}"
        }
    }

    private fun build(key: String, cs: List<CalendarCollection>, byId: Map<Long, ConnectorSyncStatus>, manyLegacy: Boolean): AccountGroup {
        val first = cs.first()
        val s = first.source
        val kind = key.substringBefore(':')
        val id = first.connectorId
        val acc = id?.let { byId[it] }
        val (name, sub) = when (kind) {
            "builtin" -> AppText.get(R.string.cal_group_mailr) to null
            "shared" -> AppText.get(R.string.cal_group_shared) to null
            "phone" -> AppText.get(R.string.cal_group_phone) to s?.device?.ifBlank { null }
            "link" -> AppText.get(R.string.cal_group_links) to null
            "account" -> (s?.name?.ifBlank { null } ?: acc?.name?.ifBlank { null } ?: s?.host ?: AppText.get(R.string.cal_account_n, id ?: "").trim()) to
                s?.host?.takeIf { s.name?.isNotBlank() == true || acc?.name?.isNotBlank() == true }
            else -> (acc?.name?.ifBlank { null }
                ?: if (manyLegacy) AppText.get(R.string.cal_account_n, id ?: "").trim() else AppText.get(R.string.cal_calendars)) to null
        }
        // An account's own failure belongs to its group (not to the calendars shared through it).
        val own = acc?.takeIf { it.status == "error" && kind in setOf("builtin", "account", "connector") }
        val failed = cs.firstOrNull { it.error != null || it.sync?.error != null }
        val error = own?.detail ?: failed?.let { it.error ?: it.sync?.error }
        val errorKind = if (own != null) own.errorKind else failed?.let { it.errorKind ?: it.sync?.errorKind }
        val errorHost = (if (own != null) own.errorHost else failed?.errorHost) ?: s?.host
        return AccountGroup(id, name, error, cs, authFailed = errorKind == "auth" && error != null, kind = kind, sub = sub,
            errorKind = errorKind.takeIf { error != null }, errorHost = errorHost.takeIf { error != null })
    }
}

/** A calendar sync failure in plain words, from its kind (the server's calendar/sync_errors.py). */
object SyncProblems {
    val KINDS = listOf("auth", "not_found", "tls", "unreachable", "server", "blocked", "partial", "unknown")

    /** What went wrong at [host] and what to do; without a host, [fallback] (the account) names the place. */
    fun text(kind: String?, host: String?, fallback: String? = null): String {
        val where = host?.ifBlank { null } ?: fallback?.ifBlank { null } ?: AppText.get(R.string.cal_sync_problem_somewhere)
        val res = when (kind) {
            "auth" -> R.string.cal_sync_problem_auth
            "not_found" -> R.string.cal_sync_problem_not_found
            "tls" -> R.string.cal_sync_problem_tls
            "unreachable" -> R.string.cal_sync_problem_unreachable
            "server" -> R.string.cal_sync_problem_server
            "blocked" -> R.string.cal_sync_problem_blocked
            "partial" -> R.string.cal_sync_problem_partial
            else -> R.string.cal_sync_problem_unknown
        }
        return AppText.get(res, where)
    }
}
