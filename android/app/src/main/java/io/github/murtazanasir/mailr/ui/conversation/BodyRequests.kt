package io.github.murtazanasir.mailr.ui.conversation

import io.github.murtazanasir.mailr.util.catching

import android.net.Uri
import android.webkit.WebResourceResponse
import java.io.ByteArrayInputStream
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/**
 * What the message WebView may load. The body is loaded under a made-up origin
 * ([ORIGIN]); the only requests it may make are the server's image proxy paths
 * (`/api/v1/messages/{id}/images/{hash}`, backend core/imageproxy.py), which the app fetches
 * with its own signed-in client. Every other request (a sender's server, a tracker, any other
 * mailr path) gets an empty response and never reaches the network. ACT mail.show_images @android.reader
 */
object BodyRequests {
    const val ORIGIN = "https://body.mailr.invalid"
    private const val HOST = "body.mailr.invalid"
    private val PROXY_PATH = Regex("^/api/v1/messages/\\d+/images/[0-9a-f]{32}$")
    private const val MAX_BYTES = 6 * 1024 * 1024
    private val TYPES = setOf("image/png", "image/jpeg", "image/gif", "image/webp", "image/avif")

    class Image(val type: String, val bytes: ByteArray)

    /** The proxy path a body URL names, or null when the URL is anything else. */
    fun proxyPath(url: String): String? {
        val u = catching { Uri.parse(url) }.getOrNull() ?: return null
        if (u.scheme != "https" || u.host != HOST || u.port != -1 || u.query != null || u.fragment != null) return null
        val path = u.encodedPath ?: return null
        return path.takeIf { PROXY_PATH.matches(it) }
    }

    /**
     * The response for a WebView request: the proxied image, or an empty response. data: URIs
     * (inline images) return null so the WebView decodes them itself; they never touch the network.
     */
    fun intercept(url: String, fetch: ((String) -> Image?)?): WebResourceResponse? {
        if (url.startsWith("data:", ignoreCase = true)) return null
        val path = proxyPath(url)
        val image = if (path != null && fetch != null) catching { fetch(path) }.getOrNull() else null
        if (image == null || image.type !in TYPES || image.bytes.size > MAX_BYTES) return blocked()
        return WebResourceResponse(image.type, null, 200, "OK",
            mapOf("Cache-Control" to "private, max-age=86400", "X-Content-Type-Options" to "nosniff"),
            ByteArrayInputStream(image.bytes))
    }

    private fun blocked() = WebResourceResponse("text/plain", "utf-8", 403, "Blocked", emptyMap(), ByteArrayInputStream(ByteArray(0)))

    /** "1 tracker blocked", "3 trackers blocked". */
    fun trackersLabel(count: Int) = AppText.plural(R.plurals.conv_trackers_blocked, count, count)
}
