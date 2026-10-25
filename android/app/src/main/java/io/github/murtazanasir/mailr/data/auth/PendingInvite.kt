package io.github.murtazanasir.mailr.data.auth

import android.content.Context

/**
 * A household invite from a setup link (mailr://setup?server=…&invite=…), kept until
 * the app is signed in to that server, then accepted once (POST /invites/{token}/accept,
 * ACT household.join @android.setup). It survives the trip through the browser for single sign-on.
 */
class PendingInvite(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("mailr_invite", Context.MODE_PRIVATE)

    fun save(server: String, token: String) = prefs.edit().putString(KEY_SERVER, server).putString(KEY_TOKEN, token).apply()

    /** The waiting token when it is for [server], kept (sent with single sign-on to that server only). */
    fun peek(server: String): String? {
        val s = prefs.getString(KEY_SERVER, null) ?: return null
        return prefs.getString(KEY_TOKEN, null)?.takeIf { s.equals(server.trimEnd('/'), ignoreCase = true) }
    }

    /** The token for [server], or null; a token for another server is dropped. */
    fun forServer(server: String): String? {
        val s = prefs.getString(KEY_SERVER, null) ?: return null
        val t = prefs.getString(KEY_TOKEN, null) ?: return null
        if (!s.equals(server.trimEnd('/'), ignoreCase = true)) {
            clear()
            return null
        }
        return t
    }

    fun clear() = prefs.edit().clear().apply()

    private companion object {
        const val KEY_SERVER = "server"
        const val KEY_TOKEN = "token"
    }
}
