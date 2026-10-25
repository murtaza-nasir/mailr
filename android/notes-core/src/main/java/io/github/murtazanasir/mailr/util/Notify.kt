package io.github.murtazanasir.mailr.util

import android.Manifest
import android.app.Notification
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/**
 * Posts a notification when the app may (Android 13 and later ask for POST_NOTIFICATIONS); without
 * the permission nothing shows and the answer is false. Every notification of both apps goes
 * through here, so the check is in one place (Android Lint's MissingPermission).
 */
object Notify {
    fun allowed(context: Context): Boolean = Build.VERSION.SDK_INT < 33 ||
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    fun post(context: Context, tag: String?, id: Int, n: Notification): Boolean {
        if (!allowed(context)) return false
        return try {
            NotificationManagerCompat.from(context).notify(tag, id, n)
            true
        } catch (e: SecurityException) {
            false
        }
    }
}
