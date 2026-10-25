package io.github.murtazanasir.mailr.ui.components

import android.content.Context
import android.view.MotionEvent
import android.widget.Toast
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.input.pointer.pointerInteropFilter
import androidx.compose.ui.platform.LocalContext
import io.github.murtazanasir.mailr.session.R
import io.github.murtazanasir.mailr.util.catching

/**
 * Tapjacking guard for buttons that approve, send or delete: a touch that reaches the window
 * through another app's overlay (MotionEvent.FLAG_WINDOW_IS_OBSCURED), or while an overlay covers
 * any part of the window (FLAG_WINDOW_IS_PARTIALLY_OBSCURED, Android 10+: an overlay can hide the
 * card text and leave the button clear), is dropped, and a short
 * message says why. The Compose form of View.filterTouchesWhenObscured, for the buttons that
 * need it only, so screen-dimming apps do not block the rest of the app.
 */
@OptIn(ExperimentalComposeUiApi::class)
fun Modifier.refuseObscuredTouches(onRefused: (() -> Unit)? = null): Modifier = composed {
    val ctx = LocalContext.current
    Modifier.pointerInteropFilter { ev ->
        val obscured = TouchGuard.isObscured(ev)
        if (obscured && ev.actionMasked == MotionEvent.ACTION_DOWN) {
            onRefused?.invoke() ?: TouchGuard.tellUser(ctx)
        }
        obscured
    }
}

object TouchGuard {
    /** Both obscured flags: the touch went through an overlay, or an overlay covers part of the window. */
    const val OBSCURED_FLAGS: Int = MotionEvent.FLAG_WINDOW_IS_OBSCURED or MotionEvent.FLAG_WINDOW_IS_PARTIALLY_OBSCURED

    fun isObscured(ev: MotionEvent): Boolean = isObscured(ev.flags)

    fun isObscured(flags: Int): Boolean = flags and OBSCURED_FLAGS != 0

    fun tellUser(ctx: Context) {
        catching {
            Toast.makeText(ctx, ctx.getString(R.string.session_overlay_warning), Toast.LENGTH_LONG).show()
        }
    }
}
