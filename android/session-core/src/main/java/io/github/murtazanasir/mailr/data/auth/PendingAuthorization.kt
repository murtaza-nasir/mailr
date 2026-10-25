package io.github.murtazanasir.mailr.data.auth

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import android.net.Uri

/**
 * The sign-in in progress: the request's state, the nonce seed, the
 * server it was started for and the redirect it expects. Kept in the app's private preferences so
 * it survives the trip through the browser, and dropped when the sign-in ends.
 *
 * - The redirect activity forwards an answer only when [accepts] says it is the expected redirect
 *   with this sign-in's state; an answer without it (another app's crafted error) is dropped.
 * - [AuthManager.complete] sends the seed only for the server the sign-in started with.
 */
class PendingAuthorization(context: Context) {
    data class Pending(val state: String, val seed: String, val origin: String, val redirect: String, val startedAt: Long)

    private val prefs = context.applicationContext.getSharedPreferences("mailr_auth_pending", Context.MODE_PRIVATE)

    fun begin(p: Pending) = prefs.edit().putString(K_STATE, p.state).putString(K_SEED, p.seed).putString(K_ORIGIN, p.origin)
        .putString(K_REDIRECT, p.redirect).putLong(K_AT, p.startedAt).commit()

    /** The sign-in in progress, or null (none, or older than [MAX_AGE_MS]). */
    fun current(now: Long = System.currentTimeMillis()): Pending? {  // clock-ok: wall time across process restarts
        val p = Pending(prefs.getString(K_STATE, null) ?: return null, prefs.getString(K_SEED, null) ?: return null,
            prefs.getString(K_ORIGIN, null) ?: return null, prefs.getString(K_REDIRECT, null) ?: return null, prefs.getLong(K_AT, 0))
        return p.takeIf { now - it.startedAt in 0..MAX_AGE_MS }
    }

    fun clear() = prefs.edit().clear().commit()

    /** Whether [uri] is the answer to the sign-in in progress: its redirect (scheme and path) and its state. */
    fun accepts(uri: Uri?): Boolean {
        val p = current() ?: return false
        if (uri == null || uri.isOpaque) return false
        val expected = Uri.parse(p.redirect)
        if (!uri.scheme.equals(expected.scheme, ignoreCase = true)) return false
        if ((uri.path ?: "") != (expected.path ?: "") || !uri.authority.isNullOrEmpty() && uri.authority != expected.authority) return false
        val state = catching { uri.getQueryParameter("state") }.getOrNull() ?: return false
        return java.security.MessageDigest.isEqual(state.toByteArray(), p.state.toByteArray())
    }

    companion object {
        /** A sign-in takes a few minutes at most; an older one is not answered. */
        const val MAX_AGE_MS = 30 * 60 * 1000L
        private const val K_STATE = "state"
        private const val K_SEED = "seed"
        private const val K_ORIGIN = "origin"
        private const val K_REDIRECT = "redirect"
        private const val K_AT = "at"
    }
}
