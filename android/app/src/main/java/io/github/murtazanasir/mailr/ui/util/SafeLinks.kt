package io.github.murtazanasir.mailr.ui.util

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.browser.customtabs.CustomTabsIntent

/**
 * Opening links that come from content other people wrote: assistant answers, briefs, events,
 * recordings. Only web links, mailto: and tel: open. Anything else (content:, intent:, sms:,
 * market:, file:, javascript:, the app's own mailr: links) is refused, so a link in an answer
 * cannot start another app with data it chose.
 */
object SafeLinks {
    enum class Kind { WEB, MAILTO, TEL, REFUSED }

    fun kindOf(url: String): Kind {
        val scheme = url.trim().substringBefore(':', "").lowercase()
        return when (scheme) {
            "https", "http" -> if (Uri.parse(url.trim()).host.isNullOrBlank()) Kind.REFUSED else Kind.WEB
            "mailto" -> Kind.MAILTO
            "tel" -> Kind.TEL
            else -> Kind.REFUSED
        }
    }

    /**
     * The intent that opens [url], or null when it is refused. Web links go to a Custom Tab
     * (see [open]); mailto: opens mailr's composer; tel: opens the dialer without calling.
     */
    fun intentFor(ctx: Context, url: String): Intent? {
        val u = url.trim()
        return when (kindOf(u)) {
            Kind.WEB -> Intent(Intent.ACTION_VIEW, Uri.parse(u)).addCategory(Intent.CATEGORY_BROWSABLE)
            Kind.MAILTO -> Intent(Intent.ACTION_VIEW, Uri.parse(u)).setClass(ctx, io.github.murtazanasir.mailr.ui.MainActivity::class.java)
            Kind.TEL -> Intent(Intent.ACTION_DIAL, Uri.parse(u))
            Kind.REFUSED -> null
        }?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }

    /**
     * Open [url] if it is allowed. Returns false when it was refused or nothing could open it.
     * [customTab] false hands a web link to whichever app claims it (a meeting app for Join).
     */
    fun open(ctx: Context, url: String, customTab: Boolean = true): Boolean {
        val u = url.trim()
        val intent = intentFor(ctx, u) ?: return false
        return catching {
            if (kindOf(u) == Kind.WEB && customTab) {
                val tab = CustomTabsIntent.Builder().setShowTitle(true).build()
                if (ctx !is android.app.Activity) tab.intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                tab.launchUrl(ctx, Uri.parse(u))
            } else ctx.startActivity(intent)
        }.isSuccess
    }
}

/** The app's [androidx.compose.ui.platform.UriHandler]: [SafeLinks.open], so refused links do nothing. */
class SafeUriHandler(private val ctx: Context) : androidx.compose.ui.platform.UriHandler {
    override fun openUri(uri: String) {
        SafeLinks.open(ctx, uri)
    }
}
