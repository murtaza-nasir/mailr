package io.github.murtazanasir.mailr.ui.components

import io.github.murtazanasir.mailr.util.catching

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.os.Build
import android.view.WindowManager
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

// The overlay touch guard (Modifier.refuseObscuredTouches, TouchGuard) is in session-core, in this
// package: the shared sign-in screens use it too.

/** The activity behind a Compose context. */
private tailrec fun Context.hostActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.hostActivity()
    else -> null
}

/**
 * While this is in the composition, other apps' overlay windows are hidden (Android 12+,
 * HIDE_OVERLAY_WINDOWS) and the screen is kept out of screenshots and Recents (see [SecureScreens]).
 */
@Composable
fun SensitiveScreen() {
    val activity = LocalContext.current.hostActivity() ?: return
    val view = androidx.compose.ui.platform.LocalView.current
    DisposableEffect(activity, view) {
        SecureScreens.enter(activity)
        // Inside a bottom sheet or dialog: that window was made before this ran, so it gets the flag itself.
        view.post { SecureScreens.secureOwnWindow(activity, view) }
        onDispose { SecureScreens.leave(activity) }
    }
}

/**
 * While this is in the composition, other apps' overlay windows are hidden (Android 12+), with no
 * change to screenshots: for screens with a Send button that are not otherwise sensitive (the composer).
 */
@Composable
fun HideOverlays() {
    val activity = LocalContext.current.hostActivity() ?: return
    DisposableEffect(activity) {
        SecureScreens.enterOverlayOnly(activity)
        onDispose { SecureScreens.leaveOverlayOnly(activity) }
    }
}

/**
 * FLAG_SECURE for the app's windows: always when Settings → "Hide content in Recents and
 * screenshots" is on, and while a sensitive screen (approvals, a local-only recording) is open
 * when "Hide approvals and local-only recordings" is on (the default).
 */
object SecureScreens {
    @Volatile var hideAll: Boolean = false
        private set
    @Volatile var hideSensitive: Boolean = true
        private set
    private val open = java.util.WeakHashMap<Activity, Int>()
    private val overlayOnly = java.util.WeakHashMap<Activity, Int>()

    fun configure(hideAll: Boolean, hideSensitive: Boolean, activity: Activity?) {
        this.hideAll = hideAll
        this.hideSensitive = hideSensitive
        activity?.let(::apply)
    }

    fun enter(a: Activity) {
        synchronized(open) { open[a] = (open[a] ?: 0) + 1 }
        apply(a)
    }

    fun leave(a: Activity) {
        synchronized(open) { open[a] = ((open[a] ?: 1) - 1).coerceAtLeast(0) }
        apply(a)
    }

    fun enterOverlayOnly(a: Activity) {
        synchronized(open) { overlayOnly[a] = (overlayOnly[a] ?: 0) + 1 }
        applyOverlays(a)
    }

    fun leaveOverlayOnly(a: Activity) {
        synchronized(open) { overlayOnly[a] = ((overlayOnly[a] ?: 1) - 1).coerceAtLeast(0) }
        applyOverlays(a)
    }

    /**
     * Overlays stay hidden while any sensitive screen or overlay-only screen is open. Counted, so
     * an approval card leaving the chat does not show overlays again over the Approvals screen.
     */
    fun hidesOverlays(a: Activity): Boolean = synchronized(open) { (open[a] ?: 0) > 0 || (overlayOnly[a] ?: 0) > 0 }

    private fun applyOverlays(a: Activity) {
        if (Build.VERSION.SDK_INT >= 31) catching { a.window.setHideOverlayWindows(hidesOverlays(a)) }
    }

    fun shouldHide(a: Activity): Boolean = hideAll || (hideSensitive && synchronized(open) { (open[a] ?: 0) > 0 })

    /** FLAG_SECURE on the window that holds [view] when it is not the activity's own (a sheet or dialog). */
    fun secureOwnWindow(a: Activity, view: android.view.View) {
        val root = view.rootView ?: return
        if (root === a.window.decorView || !shouldHide(a)) return
        val lp = root.layoutParams as? WindowManager.LayoutParams ?: return
        if (lp.flags and WindowManager.LayoutParams.FLAG_SECURE != 0) return
        lp.flags = lp.flags or WindowManager.LayoutParams.FLAG_SECURE
        catching { a.windowManager.updateViewLayout(root, lp) }
    }

    fun apply(a: Activity) {
        val on = shouldHide(a)
        if (on) a.window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        else a.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
        if (Build.VERSION.SDK_INT >= 33) catching { a.setRecentsScreenshotEnabled(!on) }
        applyOverlays(a)
    }
}

/** Keep this activity's FLAG_SECURE in step with the privacy settings. */
fun androidx.activity.ComponentActivity.followSecurePrefs(prefs: kotlinx.coroutines.flow.Flow<io.github.murtazanasir.mailr.data.settings.Prefs>) {
    val activity = this
    SecureScreens.apply(activity)
    activity.lifecycleScope.launch {
        prefs.collect { SecureScreens.configure(it.hideContent, it.hideSensitive, activity) }
    }
}
