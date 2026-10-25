package io.github.murtazanasir.mailr.data.auth

import io.github.murtazanasir.mailr.util.catching

import android.util.Base64
import java.security.MessageDigest
import java.security.SecureRandom

/**
 * The nonce that binds a sign-in to the server the app is talking to.
 *
 * The app picks a random [newSeed] `r` and asks the provider for nonce = base64url(SHA-256(origin +
 * "|" + r)), with the [canonicalOrigin] of its server. It posts `r` with the ID token. The server
 * recomputes the nonce with its own address (backend api/native_auth.check_binding) and refuses a
 * token that was asked for while the app talked to another server. Shared cases:
 * backend/tests/fixtures/native_nonce_cases.json.
 */
object NonceBinding {
    private val random = SecureRandom()

    fun newSeed(): String = ByteArray(24).also(random::nextBytes).let(::b64)

    fun nonce(origin: String, seed: String): String =
        b64(MessageDigest.getInstance("SHA-256").digest("${canonicalOrigin(origin)}|$seed".toByteArray(Charsets.UTF_8)))

    /**
     * scheme://host[:port][/path] in one spelling: scheme and host in lower case, the default port
     * and trailing slashes left out ("" for an address with no scheme or host).
     */
    fun canonicalOrigin(url: String): String {
        val u = catching { java.net.URI(url.trim()) }.getOrNull() ?: return ""
        val scheme = u.scheme?.lowercase().orEmpty()
        val host = u.host?.lowercase().orEmpty()
        if (scheme.isEmpty() || host.isEmpty()) return ""
        val port = u.port
        val defaultPort = (scheme == "https" && port == 443) || (scheme == "http" && port == 80)
        val netloc = if (port < 0 || defaultPort) host else "$host:$port"
        return "$scheme://$netloc${(u.rawPath ?: "").trimEnd('/')}"
    }

    private fun b64(bytes: ByteArray): String = Base64.encodeToString(bytes, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
}
