package io.github.murtazanasir.mailr.ui.conversation

import io.github.murtazanasir.mailr.util.catching

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Color
import android.net.Uri
import android.view.ViewGroup
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.unit.dp
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.getValue
import androidx.compose.foundation.layout.height
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import io.github.murtazanasir.mailr.MailrApp

/**
 * Renders a server-sanitised message body. JavaScript, file and content access are off.
 * Images come only from mailr's image proxy, fetched by the app's own client; the WebView never
 * contacts a sender's server ([BodyRequests]). Links open in a Custom Tab; mailto: links go to
 * [onMailto].
 */
/**
 * Draws the body instead of the WebView when set. Robolectric cannot draw a WebView, so the
 * screenshot tests (DocsShotsTest) render the HTML as text; the app leaves it null.
 */
val LocalBodyRenderer = androidx.compose.runtime.staticCompositionLocalOf<(@Composable (html: String, modifier: Modifier) -> Unit)?> { null }

@SuppressLint("SetJavaScriptEnabled")
@Composable
fun MessageWebView(
    html: String,
    showQuote: Boolean,
    darkText: Boolean,
    allowImages: Boolean,
    onMailto: (Uri) -> Unit,
    modifier: Modifier = Modifier,
    loadImage: ((String) -> BodyRequests.Image?)? = defaultImageLoader(),
) {
    LocalBodyRenderer.current?.let { it(html, modifier); return }
    // Fit to width (newsletters and bank mail with fixed widths were cut off at the
    // sides): the page first lays out at the screen's width; a body that is still wider (a
    // fixed 600 px table) loads again with the CSS zoom that makes it fit, as Gmail shows it.
    // Pinch-to-zoom still enlarges it. Responsive mail fits at once and stays at zoom 1.
    // Saved, so a message scrolled back into view loads at its fitted zoom straight away.
    var zoom by androidx.compose.runtime.saveable.rememberSaveable(html, showQuote) { mutableFloatStateOf(1f) }
    val doc = remember(html, showQuote, darkText, allowImages, zoom) { wrap(html, showQuote, darkText, allowImages, zoom) }
    // Compose measures the view once, before the page has laid out, and a WRAP_CONTENT WebView
    // never asks again. With JavaScript off, read contentHeight (CSS px, which equal dp at
    // scale 1) after each load and after images arrive, and size the view from that.
    var heightDp by remember { mutableIntStateOf(0) }
    AndroidView(
        modifier = modifier.fillMaxWidth().then(if (heightDp > 0) Modifier.height(heightDp.dp) else Modifier.height(1.dp)),
        factory = { ctx -> build(ctx, onMailto, onHeight = { h -> heightDp = h }, onFit = { z -> zoom = z }) },
        update = { wv ->
            wv.settings.blockNetworkImage = !allowImages
            (wv.webViewClient as? SizingClient)?.let { c ->
                c.loadImage = if (allowImages) loadImage else null
                c.zoom = zoom
            }
            if (wv.tag != doc) {
                wv.tag = doc
                heightDp = 0   // contentHeight never drops below the viewport, so start small
                // A made-up origin: the proxy's relative image paths resolve under it, and
                // BodyRequests answers every request the page makes.
                wv.loadDataWithBaseURL(BodyRequests.ORIGIN + "/", doc, "text/html", "utf-8", null)
            } else if (allowImages) {
                (wv.webViewClient as? SizingClient)?.remeasure(wv)
            }
        },
        onRelease = { it.destroy() },
    )
}

/** A WebView that tells how wide its content is (in view pixels at the current page scale). */
internal class FitWebView(ctx: Context) : WebView(ctx) {
    fun contentWidthPx(): Int = computeHorizontalScrollRange()
}

private fun build(ctx: Context, onMailto: (Uri) -> Unit, onHeight: (Int) -> Unit, onFit: (Float) -> Unit): WebView = FitWebView(ctx).apply {
    layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
    setBackgroundColor(Color.TRANSPARENT)
    isVerticalScrollBarEnabled = false
    isHorizontalScrollBarEnabled = false
    overScrollMode = WebView.OVER_SCROLL_NEVER
    settings.apply {
        javaScriptEnabled = false
        allowFileAccess = false
        allowContentAccess = false
        domStorageEnabled = false
        databaseEnabled = false
        setGeolocationEnabled(false)
        mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        // The page lays out at the view's width and page scale 1; MessageWebView fits a wider body
        // with CSS zoom. Pinch-to-zoom enlarges it (no on-screen buttons).
        loadWithOverviewMode = false
        useWideViewPort = false
        textZoom = 100
        setSupportZoom(true)
        builtInZoomControls = true
        displayZoomControls = false
        cacheMode = WebSettings.LOAD_NO_CACHE
    }
    webViewClient = SizingClient(onMailto, onHeight, onFit)
}

/** The signed-in app client fetches proxied images (GET messages/{id}/images/{hash}). */
@Composable
private fun defaultImageLoader(): ((String) -> BodyRequests.Image?)? {
    val app = LocalContext.current.applicationContext as? MailrApp ?: return null
    return remember(app) { { path -> app.graph.api.proxiedImage(path)?.let { (type, bytes) -> BodyRequests.Image(type, bytes) } } }
}

/**
 * After load: a body wider than the view asks for a zoom that fits it ([onFit], then a reload);
 * otherwise reports the document height, and again while late images and fonts settle. Until
 * the body fits, the view keeps its 1 dp height, so the cut-off first layout never shows.
 */
private class SizingClient(val onMailto: (Uri) -> Unit, val onHeight: (Int) -> Unit, val onFit: (Float) -> Unit) : WebViewClient() {
    @Volatile var loadImage: ((String) -> BodyRequests.Image?)? = null
    /** The zoom the loaded document has. */
    var zoom = 1f

    override fun onPageFinished(view: WebView, url: String?) {
        view.post {
            val wide = (view as? FitWebView)?.contentWidthPx() ?: 0
            val fit = fitZoom(view.width, wide, zoom)
            if (fit != null) onFit(fit) else {
                view.scrollTo(0, 0)   // no horizontal offset
                remeasure(view)
            }
        }
    }

    // Every subresource the body asks for: only the image proxy is answered (off the main thread).
    override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
        BodyRequests.intercept(request.url.toString(), loadImage)

    fun remeasure(view: WebView) {
        for (delay in longArrayOf(0, 120, 400, 1000, 2500)) view.postDelayed({
            val h = view.contentHeight
            if (h > 0) onHeight(h)
        }, delay)
    }

    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
        // Only links the user taps. A message must not navigate by itself (a meta refresh or
        // other automatic navigation would open an attacker's page just by viewing the mail).
        if (!request.hasGesture()) return true
        val uri = request.url
        when (uri.scheme?.lowercase()) {
            "mailto" -> onMailto(uri)
            "http", "https" -> catching {
                CustomTabsIntent.Builder().setShowTitle(true).build().launchUrl(view.context, uri)
            }
            "tel" -> catching {
                view.context.startActivity(android.content.Intent(android.content.Intent.ACTION_DIAL, uri)
                    .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
            }
        }
        return true
    }
}

/**
 * A full document around the body. HTML mail keeps its own colours on a light "paper" card
 * (the conversation view draws the card); plain-text mail follows the app theme.
 */
fun wrap(body: String, showQuote: Boolean, darkText: Boolean, allowImages: Boolean = false, zoom: Float = 1f): String {
    val fg = if (darkText) "#1f1f1f" else "#e3e3e3"
    val link = if (darkText) "#1a5dc2" else "#aac7ff"
    val quoteCss = if (showQuote) "" else "[data-mailr-quote]{display:none!important}"
    val zoomCss = if (zoom < 1f) "html{zoom:${"%.3f".format(java.util.Locale.ROOT, zoom)}}" else ""
    return """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:${if (allowImages) " " + BodyRequests.ORIGIN else ""}; style-src 'unsafe-inline'; font-src data:; base-uri 'none'; form-action 'none'">
<style>
html,body{margin:0;padding:0;background:transparent;color:$fg;font:15px/1.5 -apple-system,Roboto,'Segoe UI',sans-serif;word-wrap:break-word;overflow-wrap:break-word}
body{padding:2px 0}
a{color:$link}
img{max-width:100%!important;height:auto!important}
table{max-width:100%!important}
pre,code{white-space:pre-wrap}
blockquote{margin:0 0 0 .6em;padding-left:.8em;border-left:3px solid #9aa0a6;color:inherit;opacity:.85}
mark.mailr-hl{background:#fde68a;color:#1f1f1f;border-radius:3px;padding:0 2px;box-shadow:0 0 0 2px #f59e0b}
$quoteCss
$zoomCss
</style></head><body>$body</body></html>"""
}

/**
 * The CSS zoom that fits a body into a view [viewPx] wide, when the document loaded at [current]
 * zoom still reaches [contentPx] (its scroll width) and so overflows by more than 2 px; null when
 * it fits (or nothing is laid out yet). Rounded down, never below 0.25.
 *
 * The first pass takes the overflow to be on the right (a fixed 600 px table). A body that still
 * overflows after it is centred (a flex row or a transform that centres a wide block): half of it
 * sits left of the page, where nothing can scroll to it, and the scroll width shows only the right
 * half, so the mail is cut on both sides. The second pass
 * counts that hidden half too, which makes such a body fit exactly.
 */
fun fitZoom(viewPx: Int, contentPx: Int, current: Float = 1f): Float? {
    if (viewPx <= 0 || contentPx <= viewPx + 2) return null
    val full = if (current < 1f) 2f * contentPx - viewPx else contentPx.toFloat()
    val z = kotlin.math.floor(current * viewPx / full * 1000f) / 1000f
    return z.coerceAtLeast(0.25f).takeIf { it < current }
}
