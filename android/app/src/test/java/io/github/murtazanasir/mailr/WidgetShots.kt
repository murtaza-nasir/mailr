package io.github.murtazanasir.mailr

import android.appwidget.AppWidgetHostView
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.view.View
import android.widget.FrameLayout
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.glance.appwidget.ExperimentalGlanceRemoteViewsApi
import androidx.glance.appwidget.GlanceRemoteViews
import kotlinx.coroutines.runBlocking

/** A Glance widget placed on a plain home screen: [x], [y], [w], [h] in dp. */
class PlacedWidget(val x: Int, val y: Int, val w: Int, val h: Int, val content: @Composable () -> Unit)

/**
 * Renders Glance widgets as a launcher would (RemoteViews applied to real views) onto a
 * home-screen colour, [wDp] by [hDp]; for the widget screenshots and the docs shots. With
 * [wallpaper], a made-up photo-like wallpaper (soft colour fields) sits under them, so translucent
 * widgets show what they let through. Each widget is applied inside an AppWidgetHostView, as a
 * launcher does: Android fills a widget's scrolling lists (Glance LazyColumn) only there.
 */
@OptIn(ExperimentalGlanceRemoteViewsApi::class)
fun homeScreen(ctx: Context, dark: Boolean, wDp: Int, hDp: Int, widgets: List<PlacedWidget>, wallpaper: Boolean = false): Bitmap {
    val d = ctx.resources.displayMetrics.density
    val frame = FrameLayout(ctx).apply {
        if (wallpaper) background = Wallpaper(dark) else setBackgroundColor(if (dark) 0xFF1B1D22.toInt() else 0xFFC9D6E3.toInt())
    }
    for (p in widgets) {
        val rv = runBlocking { GlanceRemoteViews().compose(ctx, DpSize(p.w.dp, p.h.dp)) { p.content() }.remoteViews }
        val host = AppWidgetHostView(ctx).apply { setPadding(0, 0, 0, 0) }
        val v: View = rv.apply(ctx, host)
        host.addView(v, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        frame.addView(host, FrameLayout.LayoutParams((p.w * d).toInt(), (p.h * d).toInt()).apply {
            leftMargin = (p.x * d).toInt(); topMargin = (p.y * d).toInt()
        })
    }
    frame.measure(View.MeasureSpec.makeMeasureSpec((wDp * d).toInt(), View.MeasureSpec.EXACTLY),
        View.MeasureSpec.makeMeasureSpec((hDp * d).toInt(), View.MeasureSpec.EXACTLY))
    frame.layout(0, 0, frame.measuredWidth, frame.measuredHeight)
    val bmp = Bitmap.createBitmap(frame.measuredWidth, frame.measuredHeight, Bitmap.Config.ARGB_8888)
    frame.draw(Canvas(bmp))
    return bmp
}

/**
 * A made-up wallpaper: warm at the top, teal in the middle, deep blue below, like an out-of-focus
 * photo. Each row is one colour, so the reference images stay small.
 */
private class Wallpaper(private val dark: Boolean) : android.graphics.drawable.Drawable() {
    override fun draw(c: Canvas) {
        val h = bounds.height().toFloat()
        val p = android.graphics.Paint()
        p.shader = android.graphics.LinearGradient(0f, 0f, 0f, h,
            intArrayOf(0xFFB08A63.toInt(), 0xFF8E9A92.toInt(), 0xFF3F6E7E.toInt(), 0xFF26364F.toInt()), floatArrayOf(0f, 0.3f, 0.65f, 1f),
            android.graphics.Shader.TileMode.CLAMP)
        c.drawRect(bounds, p)
        if (dark) { p.shader = null; p.color = 0x40000000; c.drawRect(bounds, p) }
    }
    override fun setAlpha(alpha: Int) {}
    override fun setColorFilter(colorFilter: android.graphics.ColorFilter?) {}
    @Deprecated("Deprecated in Java") override fun getOpacity(): Int = android.graphics.PixelFormat.OPAQUE
}

/**
 * Empties the resources' drawable cache. A vector drawn at one size keeps a cached bitmap in its
 * shared state, so a weather icon drawn earlier in the same JVM at another size changed a few
 * antialiased pixels; shots that draw one icon at several sizes call this first, so they do not
 * depend on the order the tests ran in.
 */
fun freshDrawables(ctx: Context) {
    runCatching {
        val impl = android.content.res.Resources::class.java.getDeclaredMethod("getImpl").apply { isAccessible = true }.invoke(ctx.resources)
        listOf("mDrawableCache", "mColorDrawableCache").forEach { name ->
            val cache = impl.javaClass.getDeclaredField(name).apply { isAccessible = true }.get(impl)
            cache.javaClass.getMethod("onConfigurationChange", Int::class.javaPrimitiveType).invoke(cache, -1)
        }
    }.getOrElse { throw AssertionError("could not empty the drawable cache", it) }
}
