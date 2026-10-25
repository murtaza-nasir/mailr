package io.github.murtazanasir.mailr.data.api

import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import java.time.ZonedDateTime

/*
 * Account health on the phone: each mail account's state in plain words, as the server says it
 * (GET /connectors `health`, backend core/accounthealth.py), and the one line over all of them that
 * the web shows above Settings → Mail → Mail accounts (AccountsTab.tsx healthSummary).
 */

/** {state, text}: state is ok, syncing, paused, signin, stale or error. */
@Serializable
data class AccountHealth(val state: String = "ok", val text: String = "")

/** One account of GET /connectors, with only what the phone shows. */
@Serializable
data class MailAccount(
    val id: Long = 0,
    val kind: String = "",
    @Serializable(with = SafeTextSerializer::class) val name: String = "",
    val enabled: Boolean = true,
    val health: AccountHealth? = null,
    @SerialName("last_sync_at") val lastSyncAt: String? = null,
)

// ACT settings.account_health @android.settings
suspend fun MailrApi.mailAccounts(): List<MailAccount> =
    client.get("connectors", ListSerializer(MailAccount.serializer())).filter { it.isMail() }

/** Calendars and Speakr are accounts too, but not mail accounts (as on the web). */
fun MailAccount.isMail(): Boolean = kind != "caldav" && kind != "speakr" && kind != "webcal"

object AccountHealthText {
    private val BAD = setOf("signin", "error", "stale")

    fun bad(state: String?): Boolean = state in BAD

    /**
     * "All 2 accounts are syncing. Last synced 5 min ago.", or "Work: Password required. 1 more to
     * check." Null when no enabled account carries a health (an older server).
     */
    fun summary(accounts: List<MailAccount>, now: ZonedDateTime = AppClock.now(),
                relative: (ZonedDateTime, ZonedDateTime) -> String = io.github.murtazanasir.mailr.ui.util.Format::relative): AccountHealth? {
        val rows = accounts.filter { it.enabled && it.health != null }
        if (rows.isEmpty()) return null
        val bad = rows.mapNotNull { a -> a.health?.takeIf { bad(it.state) }?.let { a to it } }
        if (bad.isEmpty()) {
            val last = rows.mapNotNull { io.github.murtazanasir.mailr.ui.util.Format.parse(it.lastSyncAt) }.maxOrNull()
            val head = AppText.plural(R.plurals.api_health_syncing, rows.size, rows.size)
            return AccountHealth("ok", head + (last?.let { AppText.get(R.string.api_health_last_synced, relative(it, now)) } ?: ""))
        }
        val (first, health) = bad.firstOrNull { it.second.state == "signin" } ?: bad[0]
        val more = bad.size - 1
        val tail = if (more > 0) AppText.plural(R.plurals.api_health_more, more, more) else ""
        return AccountHealth(health.state, "${first.name}: ${health.text.trimEnd('.')}.$tail")
    }
}
