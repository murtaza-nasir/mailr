package io.github.murtazanasir.mailr.data.cal

import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * The conference link of an event, found as the web (eventInfo.ts) and the server
 * (planner.join_url, the `join_url` of a reminder) find it: the URL property when it is a
 * meeting link, else the first meeting link in the location, then in the description.
 *
 * A meeting link is an https URL whose host is one of the providers' own domains or a
 * subdomain of one ("us02web.zoom.us"). The provider's name elsewhere in the URL does not
 * count, so "https://zoom.us.example.net/j/1" is not a meeting link.
 */
object JoinLink {
    val PROVIDERS = listOf(
        "meet.google.com", "zoom.us", "teams.microsoft.com", "teams.live.com",
        "meet.jit.si", "whereby.com", "webex.com", "bluejeans.com", "gotomeeting.com",
    )

    private val URL = Regex("https://[^\\s<>\"]+", RegexOption.IGNORE_CASE)

    fun find(url: String?, location: String?, description: String?): String? {
        url?.trim()?.takeIf { isMeeting(it) }?.let { return it }
        return listOfNotNull(location, description).firstNotNullOfOrNull { text ->
            URL.findAll(text).map { it.value }.firstOrNull(::isMeeting)
        }
    }

    /** An https link on a provider's own host. */
    fun isMeeting(url: String): Boolean = providerOf(url) != null

    /** The provider domain [url] belongs to, or null. */
    fun providerOf(url: String): String? {
        if (!url.startsWith("https://", ignoreCase = true)) return null
        val host = url.toHttpUrlOrNull()?.takeIf { it.isHttps }?.host?.lowercase() ?: return null
        return PROVIDERS.firstOrNull { host == it || host.endsWith(".$it") }
    }

    /** The host of an https link, for a "Join on <host>" label; null for anything else. */
    fun hostOf(url: String): String? =
        url.trim().toHttpUrlOrNull()?.takeIf { it.isHttps }?.host?.removePrefix("www.")
}
