package io.github.murtazanasir.mailr.ui.util

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import android.content.Intent
import java.security.MessageDigest
import java.util.UUID

/**
 * The app's own links into MainActivity (from its notifications, the recorder and the reminder
 * sheet) carry a per-install secret. MainActivity is exported for the launcher and for links,
 * so any app can start it with any data; the screens only notifications open (an approval, New
 * recording, Scheduled, a conversation by id) open only when the secret matches.
 */
object InternalLinks {
    const val EXTRA_TOKEN = "io.github.murtazanasir.mailr.LINK_TOKEN"
    private const val PREFS = "mailr_internal_links"
    private const val KEY = "token"

    @Volatile private var cached: String? = null

    fun token(context: Context): String = cached ?: synchronized(this) {
        cached ?: run {
            val p = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            p.getString(KEY, null) ?: UUID.randomUUID().toString().also { p.edit().putString(KEY, it).apply() }
        }.also { cached = it }
    }

    /** [intent] with the secret: for intents the app builds for itself (explicit, immutable). */
    fun mark(context: Context, intent: Intent): Intent = intent.putExtra(EXTRA_TOKEN, token(context))

    /** Whether [intent] was built by the app (constant-time compare). */
    fun verify(context: Context, intent: Intent?): Boolean {
        val got = catching { intent?.getStringExtra(EXTRA_TOKEN) }.getOrNull() ?: return false
        return MessageDigest.isEqual(got.toByteArray(), token(context).toByteArray())
    }
}
