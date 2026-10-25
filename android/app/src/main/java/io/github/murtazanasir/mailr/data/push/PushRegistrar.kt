package io.github.murtazanasir.mailr.data.push

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.data.sync.BackgroundSchedule

import io.github.murtazanasir.mailr.data.api.shownDetail
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import android.app.Activity
import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import io.github.murtazanasir.mailr.data.api.MailrApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.unifiedpush.android.connector.FailedReason
import org.unifiedpush.android.connector.INSTANCE_DEFAULT
import org.unifiedpush.android.connector.UnifiedPush
import org.unifiedpush.android.connector.data.ResolvedDistributor

/** Where push stands on this phone, for Settings → Notifications → Push and the poll interval. */
enum class PushStatus {
    /** Signed out, or nothing tried yet. */
    IDLE,

    /** No UnifiedPush distributor (ntfy or another) is installed. */
    NO_DISTRIBUTOR,

    /** Several distributors and none is the default: the user picks one (once). */
    CHOOSE,

    /** Asked the distributor for an endpoint, or sending it to mailr. */
    REGISTERING,

    /** mailr has this phone's endpoint: new mail, approvals and failed sends arrive at once. */
    CONNECTED,

    /** The distributor refused, or its server is unreachable. */
    FAILED,
}

data class PushInfo(
    val status: PushStatus = PushStatus.IDLE,
    /** The distributor's package and its app name ("ntfy"). */
    val distributor: String? = null,
    val distributorName: String? = null,
    /** The endpoint and Web Push keys the distributor gave, and the mailr target that holds them. */
    val endpoint: String? = null,
    val pubKey: String? = null,
    val auth: String? = null,
    val targetId: Long? = null,
    /** The endpoint mailr has under [targetId] (equal to [endpoint] once uploaded). */
    val uploaded: String? = null,
    val error: String? = null,
    val lastMessageAt: Long? = null,
    /**
     * mailr's VAPID public key as last read from GET /push/targets, which the distributor
     * registration carries (null: the server has none, or it was not read yet).
     */
    val vapid: String? = null,
    /** The target under [targetId] was posted with `vapid: true` (mailr signs its messages). */
    val uploadedVapid: Boolean = false,
    /** When the server's key was last read; it is read again after [PushRegistrar.KEY_RECHECK_MS]. */
    val vapidCheckedAt: Long? = null,
) {
    /** Push works: the background poll can slow down. */
    val healthy: Boolean get() = status == PushStatus.CONNECTED
}

/** The events this phone asks mailr to push (reminders fire on the phone itself). */
val PUSH_EVENTS = listOf("new_mail", "important_mail", "approval", "send_failed", "assistant")

/** The UnifiedPush distributors on the phone (a fake in tests). */
interface PushDistributors {
    /** The distributor in use, once it has answered (null: none yet, or it was uninstalled). */
    fun current(): String?

    /** The user's default distributor: found, several to choose from, or none. */
    fun resolveDefault(): ResolvedDistributor
    fun save(pkg: String)
    /** Register with the distributor; [vapid]: mailr's VAPID public key, null for a server without one. */
    fun register(vapid: String?)
    fun unregister()
    fun name(pkg: String): String

    /** The system's chooser for the default distributor ("Always"), so the user is asked once. */
    fun chooseDefault(activity: Activity, done: (Boolean) -> Unit)
}

/** The real distributors, through the UnifiedPush connector. [keys]: the Web Push key store (a fake in tests). */
class UnifiedPushDistributors(
    private val context: Context,
    private val keys: () -> org.unifiedpush.android.connector.keys.KeyManager = {
        org.unifiedpush.android.connector.keys.DefaultKeyManager(context) },
) : PushDistributors {
    override fun current(): String? = UnifiedPush.getAckDistributor(context)
    override fun resolveDefault(): ResolvedDistributor = UnifiedPush.resolveDefaultDistributor(context)
    override fun save(pkg: String) = UnifiedPush.saveDistributor(context, pkg)
    override fun register(vapid: String?) =
        UnifiedPush.register(context, INSTANCE_DEFAULT, context.getString(R.string.push_register_message), vapid, keys())
    override fun unregister() = UnifiedPush.unregister(context, INSTANCE_DEFAULT, keys())
    override fun name(pkg: String): String = catching {
        val pm = context.packageManager
        pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
    }.getOrDefault(pkg.substringAfterLast('.'))

    override fun chooseDefault(activity: Activity, done: (Boolean) -> Unit) =
        UnifiedPush.tryUseDefaultDistributor(activity, done)
}

/** This phone's push state, kept in plain SharedPreferences (no secrets: the endpoint is not a credential to mailr). */
class PushStore(
    context: Context,
    /** Seals the endpoint (a capability URL) and the auth secret; null in Robolectric. */
    private val box: io.github.murtazanasir.mailr.data.auth.SecretBox? =
        if (io.github.murtazanasir.mailr.data.auth.SecretBox.isRobolectric) null else io.github.murtazanasir.mailr.data.auth.SecretBox.keystore("mailr_push_key"),
) {
    private val prefs: SharedPreferences = context.getSharedPreferences("mailr_push", Context.MODE_PRIVATE)

    private fun seal(k: String, v: String?): String? {
        val b = box ?: return v
        return if (v == null) null else catching { b.seal(k, v) }.getOrDefault(v)
    }

    /** A sealed value opened; a value from before sealing is read as it is (and sealed at the next write). */
    private fun opened(k: String): String? = prefs.getString(k, null)?.let { v ->
        if (io.github.murtazanasir.mailr.data.auth.SecretBox.isSealed(v)) box?.open(k, v) else v
    }
    private val _info = MutableStateFlow(read())
    val info: StateFlow<PushInfo> = _info.asStateFlow()

    @Synchronized
    fun update(block: (PushInfo) -> PushInfo): PushInfo {
        val n = block(_info.value)
        prefs.edit()
            .putString("status", n.status.name)
            .putString("distributor", n.distributor).putString("distributor_name", n.distributorName)
            .putString("endpoint", seal("endpoint", n.endpoint)).putString("pub_key", n.pubKey).putString("auth", seal("auth", n.auth))
            .putLong("target_id", n.targetId ?: -1).putString("uploaded", n.uploaded)
            .putString("error", n.error).putLong("last_message_at", n.lastMessageAt ?: -1)
            .putString("vapid", n.vapid).putBoolean("uploaded_vapid", n.uploadedVapid)
            .putLong("vapid_checked_at", n.vapidCheckedAt ?: -1)
            .apply()
        _info.value = n
        return n
    }

    fun clear() = update { PushInfo() }

    private fun read() = PushInfo(
        status = prefs.getString("status", null)?.let { s -> PushStatus.entries.firstOrNull { it.name == s } } ?: PushStatus.IDLE,
        distributor = prefs.getString("distributor", null),
        distributorName = prefs.getString("distributor_name", null),
        endpoint = opened("endpoint"),
        pubKey = prefs.getString("pub_key", null),
        auth = opened("auth"),
        targetId = prefs.getLong("target_id", -1).takeIf { it >= 0 },
        uploaded = prefs.getString("uploaded", null),
        error = prefs.getString("error", null),
        lastMessageAt = prefs.getLong("last_message_at", -1).takeIf { it >= 0 },
        vapid = prefs.getString("vapid", null),
        uploadedVapid = prefs.getBoolean("uploaded_vapid", false),
        vapidCheckedAt = prefs.getLong("vapid_checked_at", -1).takeIf { it >= 0 },
    )
}

/**
 * Registration with the user's UnifiedPush distributor, and this phone's push target on mailr.
 *
 * - [start] at sign-in and app start: the saved distributor, else the default one (no UI when
 *   there is one), registers again so the distributor confirms the endpoint.
 * - [onNewEndpoint] stores the endpoint and keys and asks for an upload ([enqueueUpload]: a
 *   WorkManager job that waits for a network), which POSTs /push/targets and deletes this
 *   phone's previous target when the endpoint changed.
 * - VAPID (RFC 8292): the registration carries mailr's public key (`vapid_public_key` of GET
 *   /push/targets), so the push server takes only messages mailr signed, and the upload posts
 *   `vapid: true`. The upload reads the key first (again after [KEY_RECHECK_MS]); when it is not
 *   the one the endpoint was registered with, the phone registers again with it and the new
 *   endpoint is uploaded. A server without a key gets the unsigned registration of before.
 * - [onUnregistered] and [signOut] delete the target.
 * - Every change of health reschedules the background poll ([pollEvery]: 60 minutes while
 *   push works, 15 otherwise).
 */
class PushRegistrar(
    private val api: MailrApi,
    val store: PushStore,
    private val distributors: PushDistributors,
    private val signedIn: () -> Boolean,
    private val enqueueUpload: () -> Unit,
    private val pollEvery: (minutes: Int) -> Unit,
    private val device: String = "${Build.MANUFACTURER} ${Build.MODEL}".trim().take(90),
    private val now: () -> Long = AppClock::millis,
    /** Push started or stopped working (BackgroundSchedule.pushWorking): the calendar's period follows. */
    private val pushWorkingChanged: () -> Unit = {},
) {
    private val lock = Mutex()
    val info: StateFlow<PushInfo> get() = store.info

    fun pollMinutes(): Int = if (store.info.value.healthy) SLOW_POLL else FAST_POLL

    private fun set(block: (PushInfo) -> PushInfo) {
        val was = store.info.value
        val next = store.update(block)
        if (was.healthy != next.healthy) pollEvery(if (next.healthy) SLOW_POLL else FAST_POLL)
        val t = now()
        if (BackgroundSchedule.pushWorking(was, t) != BackgroundSchedule.pushWorking(next, t)) pushWorkingChanged()
    }

    /** Register with the saved or default distributor, if there is one. Never shows UI. */
    fun start() {
        if (!signedIn()) return
        val current = catching { distributors.current() }.getOrNull()
        if (current != null) {
            set { it.copy(distributor = current, distributorName = distributors.name(current),
                status = if (it.healthy && it.distributor == current) PushStatus.CONNECTED else PushStatus.REGISTERING, error = null) }
            distributors.register(store.info.value.vapid)
            return
        }
        when (val r = catching { distributors.resolveDefault() }.getOrDefault(ResolvedDistributor.NoneAvailable)) {
            is ResolvedDistributor.Found -> {
                distributors.save(r.packageName)
                set { it.copy(distributor = r.packageName, distributorName = distributors.name(r.packageName),
                    status = PushStatus.REGISTERING, error = null) }
                distributors.register(store.info.value.vapid)
            }
            ResolvedDistributor.ToSelect -> set { it.copy(status = PushStatus.CHOOSE, error = null) }
            ResolvedDistributor.NoneAvailable -> set { it.copy(status = PushStatus.NO_DISTRIBUTOR, distributor = null,
                distributorName = null, error = null) }
        }
    }

    /** Settings → Push → Choose (several distributors): the system chooser, then register. */
    fun choose(activity: Activity) {
        distributors.chooseDefault(activity) { ok -> if (ok) start() else set { it.copy(status = PushStatus.CHOOSE) } }
    }

    /** Settings → Push → Retry. */
    fun retry() = start()

    fun onNewEndpoint(url: String, pubKey: String?, auth: String?) {
        set {
            val same = it.uploaded == url && it.targetId != null && it.pubKey == pubKey && it.auth == auth &&
                it.uploadedVapid == wantsVapid(it.vapid, pubKey, auth)
            it.copy(endpoint = url, pubKey = pubKey, auth = auth, error = null,
                status = if (same) PushStatus.CONNECTED else PushStatus.REGISTERING)
        }
        val cur = store.info.value
        // Connected already: the upload only reads the server's key again, once in a while.
        if (cur.status != PushStatus.CONNECTED || keyStale(cur)) enqueueUpload()
    }

    private fun keyStale(i: PushInfo): Boolean = i.vapidCheckedAt.let { it == null || now() - it >= KEY_RECHECK_MS }

    /** mailr's key read, or null when the server has none; [KeyUnread] when it could not be read. */
    private class KeyUnread(val retry: Boolean, val detail: String?) : Exception()

    private suspend fun serverKey(): String? = try {
        // The connector takes only an 87-character base64url key (a P-256 point, unpadded) and
        // throws on anything else; a key it would refuse is treated as none.
        api.pushTargets().vapidPublicKey?.trim()?.trimEnd('=')?.takeIf { VAPID_KEY.matches(it) }
    } catch (e: io.github.murtazanasir.mailr.data.api.ApiException) {
        // 404/405: a server older than the push key (and older proxies); the unsigned registration works there.
        if (e.code == 404 || e.code == 405) null else throw KeyUnread(e.retryable, e.shownDetail())
    } catch (e: Exception) {
        throw KeyUnread(true, null)
    }

    /**
     * Send the distributor's endpoint to mailr (POST /push/targets, kind unifiedpush, this
     * phone's name, its keys), then delete the target of the previous endpoint. False: try again later.
     */
    suspend fun upload(): Boolean = lock.withLock {
        if (!signedIn()) return@withLock true
        val first = store.info.value
        first.endpoint ?: return@withLock true
        val key = try {
            serverKey()
        } catch (e: KeyUnread) {
            if (e.retry) return@withLock false
            set { it.copy(status = PushStatus.FAILED, error = e.detail) }
            return@withLock true
        }
        if (key != first.vapid) {
            // Registered with another key (or none): register again with mailr's; the distributor
            // answers with onNewEndpoint, and that upload posts the endpoint.
            set { it.copy(vapid = key, vapidCheckedAt = now(), status = PushStatus.REGISTERING, error = null) }
            distributors.register(key)
            return@withLock true
        }
        set { it.copy(vapidCheckedAt = now()) }
        val cur = store.info.value
        val endpoint = cur.endpoint ?: return@withLock true
        val vapid = wantsVapid(key, cur.pubKey, cur.auth)
        if (cur.uploaded == endpoint && cur.targetId != null && cur.status == PushStatus.CONNECTED &&
            cur.uploadedVapid == vapid) return@withLock true
        val target = try {
            api.addPushTarget(endpoint, device, PUSH_EVENTS, cur.pubKey, cur.auth, vapid = vapid)
        } catch (e: io.github.murtazanasir.mailr.data.api.ApiException) {
            if (e.retryable) return@withLock false
            set { it.copy(status = PushStatus.FAILED, error = e.shownDetail()) }
            return@withLock true
        } catch (e: Exception) {
            return@withLock false
        }
        val old = cur.targetId
        if (old != null && old != target.id) catching { api.deletePushTarget(old) }
        set { it.copy(targetId = target.id, uploaded = endpoint, uploadedVapid = vapid, status = PushStatus.CONNECTED,
            error = null) }
        true
    }

    fun onRegistrationFailed(reason: FailedReason) {
        set { it.copy(status = PushStatus.FAILED, error = when (reason) {
            FailedReason.NETWORK -> AppText.get(R.string.push_err_network)
            FailedReason.ACTION_REQUIRED -> it.distributorName?.let { d -> AppText.get(R.string.push_err_action_required, d) }
                ?: AppText.get(R.string.push_err_action_required_default)
            FailedReason.VAPID_REQUIRED -> it.distributorName?.let { d -> AppText.get(R.string.push_err_vapid_required, d) }
                ?: AppText.get(R.string.push_err_vapid_required_default)
            FailedReason.INTERNAL_ERROR -> it.distributorName?.let { d -> AppText.get(R.string.push_err_internal, d) }
                ?: AppText.get(R.string.push_err_internal_default)
        }) }
    }

    /** The distributor's server is down for now; it calls [onNewEndpoint] when it is back. */
    fun onTempUnavailable() {
        set { it.copy(status = PushStatus.FAILED, error = it.distributorName?.let { d -> AppText.get(R.string.push_err_unavailable, d) }
            ?: AppText.get(R.string.push_err_unavailable_default)) }
    }

    /** The distributor dropped mailr (uninstalled, or the user removed it there). */
    suspend fun onUnregistered() {
        dropTarget()
        set { it.copy(status = PushStatus.NO_DISTRIBUTOR, endpoint = null, pubKey = null, auth = null, targetId = null,
            uploaded = null, uploadedVapid = false, error = null) }
        // Another distributor may be installed: take the default again (no UI).
        start()
    }

    /** A message arrived: push works (also after a failure the distributor recovered from). */
    fun onMessage() {
        set { it.copy(lastMessageAt = AppClock.millis(),
            status = if (it.targetId != null) PushStatus.CONNECTED else it.status, error = if (it.targetId != null) null else it.error) }
    }

    /** Before the session ends: delete this phone's target and leave the distributor. */
    suspend fun signOut() {
        dropTarget()
        catching { distributors.unregister() }
        store.clear()
        pollEvery(FAST_POLL)
    }

    /**
     * The session ended without [signOut] (expired, revoked): leave the distributor. The target
     * stays on mailr (no session to delete it with); messages to it reach nobody.
     */
    fun forget() {
        if (store.info.value.status == PushStatus.IDLE && store.info.value.endpoint == null) return
        catching { distributors.unregister() }
        store.clear()
    }

    /** Settings → Push → Send test: POST /push/targets/{id}/test. */
    suspend fun test(): PushTestResult {
        val id = store.info.value.targetId ?: return PushTestResult(false, AppText.get(R.string.push_test_not_registered))
        return try {
            val r = api.testPushTarget(id)
            if (r.removed) {
                set { it.copy(targetId = null, uploaded = null, uploadedVapid = false, status = PushStatus.REGISTERING) }
                start()
            }
            PushTestResult(r.ok, r.error)
        } catch (e: Exception) {
            PushTestResult(false, (e as? io.github.murtazanasir.mailr.data.api.ApiException)?.shownDetail() ?: AppText.get(R.string.push_test_offline))
        }
    }

    private suspend fun dropTarget() {
        val id = store.info.value.targetId ?: return
        catching { api.deletePushTarget(id) }
    }

    companion object {
        const val FAST_POLL = 15
        const val SLOW_POLL = 60

        /** How long a read of the server's VAPID key holds before an app start reads it again. */
        const val KEY_RECHECK_MS = 24L * 60 * 60 * 1000

        private val VAPID_KEY = Regex("^[A-Za-z0-9_-]{87}$")

        /** A signed registration needs the connector's Web Push keys too (mailr refuses vapid without them). */
        fun wantsVapid(vapid: String?, pubKey: String?, auth: String?) = vapid != null && pubKey != null && auth != null
    }
}

data class PushTestResult(val ok: Boolean, val error: String?)
