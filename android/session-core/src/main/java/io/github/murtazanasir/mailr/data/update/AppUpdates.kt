package io.github.murtazanasir.mailr.data.update

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import android.content.Intent
import android.net.Uri
import io.github.murtazanasir.mailr.data.api.ApiClient
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** One app in GET /api/v1/app/releases (backend core/phone_apps.release). */
@Serializable
data class AppRelease(
    val app: String = "",
    val name: String = "",
    @SerialName("package") val packageName: String = "",
    val url: String = "",
    @SerialName("on_server") val onServer: Boolean = false,
    @SerialName("version_name") val versionName: String? = null,
    @SerialName("version_code") val versionCode: Long? = null,
)

@Serializable
data class AppReleases(val apps: List<AppRelease> = emptyList())

/** A newer build the server offers: its version and where it downloads. */
data class AvailableUpdate(val versionCode: Long, val versionName: String, val url: String)

/**
 * Whether this app needs or has an update (shared by the mailr app and the Notes app).
 *
 * [required]: the server answered 426, so this version is below its floor (core/appversion.py).
 * The replay queues pause on it and keep every change. The flag is kept with the version code it
 * was set for, so it clears when a newer build starts, and also when a later API call goes through
 * (the floor was lowered). [onResumed] then sends what waited.
 *
 * [available]: GET /api/v1/app/releases lists a higher version code for [app], checked at most once
 * a day ([check]). Settings shows it as a quiet row.
 *
 * [open] hands the download to the system: the browser fetches the APK from the server, and the
 * downloaded file opens the package installer. Neither carries this app's User-Agent, so the 426
 * floor never blocks the download itself.
 */
class AppUpdates(
    context: Context,
    private val client: ApiClient,
    /** "mailr" or "notes": the app's key in /api/v1/app/releases and its download, /api/v1/app/<app>.apk. */
    val app: String,
    /** The name the banner and the Settings row use ("Mailr", "Mailr Notes"). */
    val displayName: String,
    private val versionCode: Long,
    /** This build's package; a release for another package (a debug build's) is not offered. Null: no check. */
    private val packageName: String? = null,
    private val clock: () -> Long = System::currentTimeMillis,  // clock-ok: check interval
) : ApiClient.VersionListener {
    private val prefs = context.applicationContext.getSharedPreferences("mailr_app_update_$app", Context.MODE_PRIVATE)

    private val _required = MutableStateFlow(false)
    val required: StateFlow<Boolean> = _required.asStateFlow()

    private val _available = MutableStateFlow<AvailableUpdate?>(null)
    val available: StateFlow<AvailableUpdate?> = _available.asStateFlow()

    /** Set by the app: the server takes this version again, so the queues go out. */
    @Volatile var onResumed: (() -> Unit)? = null

    init {
        // The refusal was for this build; a newer build starts without it.
        if (prefs.getLong(K_REQUIRED, -1L) == versionCode) _required.value = true
        else prefs.edit().remove(K_REQUIRED).apply()
        val code = prefs.getLong(K_CODE, -1L)
        if (code > versionCode) {
            _available.value = AvailableUpdate(code, prefs.getString(K_NAME, null).orEmpty(), prefs.getString(K_URL, null).orEmpty())
        } else prefs.edit().remove(K_CODE).remove(K_NAME).remove(K_URL).apply()
        client.versionListener = this
    }

    override fun upgradeRequired() {
        if (_required.value) return
        _required.value = true
        prefs.edit().putLong(K_REQUIRED, versionCode).apply()
    }

    override fun accepted() {
        if (!_required.value) return
        _required.value = false
        prefs.edit().remove(K_REQUIRED).apply()
        onResumed?.invoke()
    }

    /**
     * Ask the server which versions it hands out, unless that happened in the last [CHECK_EVERY] ms
     * ([force] asks anyway). Failures keep what was known. Returns the update on offer, if any.
     */
    suspend fun check(force: Boolean = false): AvailableUpdate? {
        val now = clock()
        if (!force && now - prefs.getLong(K_CHECKED, 0L) in 0 until CHECK_EVERY) return _available.value
        val list = try {
            client.get("app/releases", AppReleases.serializer())
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            return _available.value
        }
        prefs.edit().putLong(K_CHECKED, now).apply()
        val r = list.apps.firstOrNull { it.app == app }
        val code = r?.versionCode
        val offer = if (r != null && code != null && code > versionCode &&
            (packageName == null || r.packageName.isBlank() || r.packageName == packageName)) {
            AvailableUpdate(code, r.versionName.orEmpty(), r.url)
        } else null
        _available.value = offer
        prefs.edit().apply {
            if (offer == null) remove(K_CODE).remove(K_NAME).remove(K_URL)
            else putLong(K_CODE, offer.versionCode).putString(K_NAME, offer.versionName).putString(K_URL, offer.url)
        }.apply()
        return offer
    }

    /**
     * Where Update downloads from: the address the release list gave, else this server's
     * /api/v1/app/<app>.apk (a refused build may not have read the list). Null without a server.
     */
    fun downloadUrl(): String? =
        _available.value?.url?.takeIf { it.startsWith("https://") || it.startsWith("http://") }
            ?: catching { client.url("app/$app.apk").toString() }.getOrNull()

    /** Update: the download opens in the browser (ACTION_VIEW). False when nothing could open it. */
    fun open(context: Context): Boolean {
        val u = downloadUrl() ?: return false
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(u))
            .addCategory(Intent.CATEGORY_BROWSABLE)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return catching { context.startActivity(intent) }.isSuccess
    }

    companion object {
        /** At most one release check a day. */
        const val CHECK_EVERY = 24 * 60 * 60 * 1000L
        private const val K_REQUIRED = "required_for"
        private const val K_CHECKED = "checked_at"
        private const val K_CODE = "offer_code"
        private const val K_NAME = "offer_name"
        private const val K_URL = "offer_url"
    }
}
