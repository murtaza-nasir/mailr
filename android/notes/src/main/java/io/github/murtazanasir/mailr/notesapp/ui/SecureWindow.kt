package io.github.murtazanasir.mailr.notesapp.ui

import io.github.murtazanasir.mailr.util.catching

import android.os.Build
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import io.github.murtazanasir.mailr.notesapp.data.NotesPrefs
import kotlinx.coroutines.launch

/**
 * Keep this activity's window in step with "Hide in Recents and screenshots": FLAG_SECURE
 * and no Recents thumbnail while it is on, as in the mailr app (SecureScreens).
 */
fun ComponentActivity.followHideContent(prefs: NotesPrefs) {
    val activity = this
    fun apply(on: Boolean) {
        if (on) activity.window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        else activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
        if (Build.VERSION.SDK_INT >= 33) catching { activity.setRecentsScreenshotEnabled(!on) }
    }
    apply(prefs.hideContent.value)
    activity.lifecycleScope.launch { prefs.hideContent.collect { apply(it) } }
}
