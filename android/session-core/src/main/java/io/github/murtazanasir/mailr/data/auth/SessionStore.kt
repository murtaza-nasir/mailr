package io.github.murtazanasir.mailr.data.auth

import android.content.Context
import android.content.SharedPreferences
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** The device session. */
data class Session(
    val server: String,
    val token: String,
    val csrf: String,
    val email: String,
    val name: String,
)

/** A sign-out the server has not confirmed yet (see [LogoutWork]). */
data class PendingLogout(val server: String, val token: String, val csrf: String)

/**
 * The server address, the device session and a pending sign-out. Values are sealed with a
 * Keystore key ([SecretBox]) in "mailr_session_v2"; a phone that kept them in the older
 * EncryptedSharedPreferences file moves them across once and deletes that file.
 */
class SessionStore(
    context: Context,
    /** The server address the sign-in screen starts with (deploy.properties `defaultServer`); empty when unset. */
    private val defaultServer: String = "",
) {
    private val store: Store = open(context)

    private val _session = MutableStateFlow(read())
    val session: StateFlow<Session?> = _session.asStateFlow()

    var server: String
        get() = store.get(KEY_SERVER) ?: defaultServer
        set(value) {
            store.put(mapOf(KEY_SERVER to normalizeServer(value)))
        }

    fun save(token: String, csrf: String, email: String, name: String) {
        store.put(mapOf(KEY_TOKEN to token, KEY_CSRF to csrf, KEY_EMAIL to email, KEY_NAME to name))
        _session.value = read()
    }

    fun updateCsrf(csrf: String) {
        if (csrf.isBlank() || csrf == store.get(KEY_CSRF)) return
        store.put(mapOf(KEY_CSRF to csrf))
        _session.value = read()
    }

    fun clear() {
        store.put(mapOf(KEY_TOKEN to null, KEY_CSRF to null, KEY_EMAIL to null, KEY_NAME to null))
        _session.value = null
    }

    /** Keep [s] to end it on the server later (sign-out while offline). */
    fun savePendingLogout(s: Session) {
        store.put(mapOf(KEY_PL_SERVER to s.server, KEY_PL_TOKEN to s.token, KEY_PL_CSRF to s.csrf))
    }

    fun pendingLogout(): PendingLogout? {
        val token = store.get(KEY_PL_TOKEN) ?: return null
        return PendingLogout(store.get(KEY_PL_SERVER) ?: return null, token, store.get(KEY_PL_CSRF).orEmpty())
    }

    fun clearPendingLogout() {
        store.put(mapOf(KEY_PL_SERVER to null, KEY_PL_TOKEN to null, KEY_PL_CSRF to null))
    }

    private fun read(): Session? {
        val token = store.get(KEY_TOKEN) ?: return null
        return Session(
            server = server,
            token = token,
            csrf = store.get(KEY_CSRF) ?: "",
            email = store.get(KEY_EMAIL) ?: "",
            name = store.get(KEY_NAME) ?: "",
        )
    }

    /** String values by name; null removes. */
    internal interface Store {
        fun get(k: String): String?
        fun put(values: Map<String, String?>)
    }

    /** Plain SharedPreferences (Robolectric; or the old encrypted file if moving it fails). */
    internal class PlainStore(private val prefs: SharedPreferences) : Store {
        override fun get(k: String): String? = prefs.getString(k, null)
        override fun put(values: Map<String, String?>) {
            val e = prefs.edit()
            values.forEach { (k, v) -> if (v == null) e.remove(k) else e.putString(k, v) }
            e.apply()
        }
        fun all(): Map<String, String> = prefs.all.mapNotNull { (k, v) -> (v as? String)?.let { k to it } }.toMap()
    }

    /** Each value sealed with [box] under its own name. */
    internal class BoxStore(private val prefs: SharedPreferences, private val box: SecretBox) : Store {
        // Opened values stay in memory: the server address is read for every request.
        private val mem = HashMap<String, String?>()

        override fun get(k: String): String? = synchronized(mem) {
            if (k in mem) mem[k] else prefs.getString(k, null)?.let { box.open(k, it) }.also { mem[k] = it }
        }

        override fun put(values: Map<String, String?>) {
            synchronized(mem) { mem.putAll(values) }
            val e = prefs.edit()
            values.forEach { (k, v) -> if (v == null) e.remove(k) else e.putString(k, box.seal(k, v)) }
            // commit: the session must be on disk before the app goes on (a sign-in, a sign-out).
            e.commit()
        }
    }

    companion object {
        private const val KEY_SERVER = "server"
        private const val KEY_TOKEN = "token"
        private const val KEY_CSRF = "csrf"
        private const val KEY_EMAIL = "email"
        private const val KEY_NAME = "name"
        private const val KEY_PL_SERVER = "pending_logout_server"
        private const val KEY_PL_TOKEN = "pending_logout_token"
        private const val KEY_PL_CSRF = "pending_logout_csrf"
        private const val KEY_MOVED = "moved_from_v1"
        private const val V1 = "mailr_session"
        private const val V2 = "mailr_session_v2"
        private const val KEY_ALIAS = "mailr_session_key"

        fun normalizeServer(raw: String): String {
            var s = raw.trim().trimEnd('/')
            if (s.isNotEmpty() && !s.startsWith("http://") && !s.startsWith("https://")) s = "https://$s"
            return s
        }

        private fun open(context: Context): Store {
            // Robolectric (unit and screenshot tests) has no Android Keystore. Real devices
            // never fall back to plain storage: a Keystore failure is an error.
            if (SecretBox.isRobolectric) {
                return PlainStore(context.getSharedPreferences("mailr_session_test", Context.MODE_PRIVATE))
            }
            val prefs = context.getSharedPreferences(V2, Context.MODE_PRIVATE)
            val box = SecretBox.keystore(KEY_ALIAS)
            val store = BoxStore(prefs, box)
            if (prefs.getBoolean(KEY_MOVED, false)) return store
            return try {
                moveFromV1(context, prefs, store, box)
                store
            } catch (e: Exception) {
                // Keep working from the old file; the move is tried again at the next start.
                legacy(context)
            }
        }

        /** Copy the values of the EncryptedSharedPreferences file, check them, then delete that file. */
        private fun moveFromV1(context: Context, prefs: SharedPreferences, store: BoxStore, box: SecretBox) {
            val old = java.io.File(context.applicationInfo.dataDir, "shared_prefs/$V1.xml")
            if (old.exists()) {
                val values = legacy(context).all()
                store.put(values)
                // Read back through a fresh store (no memory): what is on disk opens to the same values.
                val fresh = BoxStore(prefs, box)
                check(values.all { (k, v) -> fresh.get(k) == v }) { "session move did not verify" }
                context.deleteSharedPreferences(V1)
            }
            prefs.edit().putBoolean(KEY_MOVED, true).commit()
        }

        @Suppress("DEPRECATION")
        private fun legacy(context: Context): PlainStore {
            val key = androidx.security.crypto.MasterKey.Builder(context)
                .setKeyScheme(androidx.security.crypto.MasterKey.KeyScheme.AES256_GCM).build()
            return PlainStore(androidx.security.crypto.EncryptedSharedPreferences.create(
                context, V1, key,
                androidx.security.crypto.EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                androidx.security.crypto.EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
            ))
        }
    }
}
