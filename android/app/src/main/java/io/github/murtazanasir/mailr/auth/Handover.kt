package io.github.murtazanasir.mailr.auth

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import android.app.Activity
import android.content.ContentProvider
import android.content.ContentValues
import android.content.Intent
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import io.github.murtazanasir.mailr.MailrApp
import io.github.murtazanasir.mailr.data.api.AuthApi
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.auth.Handover
import io.github.murtazanasir.mailr.data.auth.SessionStore
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/**
 * The mailr app's half of the Notes app's "Continue as <name>" (session-core Handover): mints a
 * single-use token with this app's session and hands it back. [result] is the whole decision, so a
 * test runs it without an activity.
 */
class HandoverMinter(private val sessions: SessionStore, private val api: AuthApi) {
    data class Result(val code: Int, val data: Intent)

    suspend fun result(callerAllowed: Boolean, request: Intent): Result {
        fun refuse(why: String) = Result(Activity.RESULT_CANCELED, Intent().putExtra(Handover.EXTRA_ERROR, why))
        if (!callerAllowed) return refuse(AppText.get(R.string.auth_handover_not_notes))
        val challenge = request.getStringExtra(Handover.EXTRA_CHALLENGE)
        if (!Handover.isChallenge(challenge)) return refuse(AppText.get(R.string.auth_handover_invalid))
        val session = sessions.session.value ?: return refuse(AppText.get(R.string.auth_handover_signed_out))
        val device = request.getStringExtra(Handover.EXTRA_DEVICE).orEmpty().ifBlank { "Android" }.take(90)
        return try {
            val t = api.mintHandover(Handover.APP_NOTES, challenge!!, device)
            Result(Activity.RESULT_OK, Intent().putExtra(Handover.EXTRA_TOKEN, t.token).putExtra(Handover.EXTRA_SERVER, session.server))
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            refuse(AppText.get(R.string.auth_handover_unreachable, e.userMessage()))
        }
    }
}

/**
 * Started by the Notes app for a result (exported, behind the signature permission
 * `<applicationId>.permission.SIGN_IN_HANDOVER`). It shows nothing: it checks the caller, mints the
 * token and finishes.
 */
class HandoverActivity : ComponentActivity() {
    // The app's language before Android 13; a no-op from 13 on.
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(io.github.murtazanasir.mailr.i18n.AppLocales.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val g = (application as MailrApp).graph
        val allowed = Handover.callerAllowed(packageManager, callingPackage, packageName)
        lifecycleScope.launch {
            val r = HandoverMinter(g.sessions, g.api.auth).result(allowed, intent)
            setResult(r.code, r.data)
            finish()
        }
    }
}

/**
 * The account the Notes app offers to continue as: `content://<applicationId>.handover/account`
 * answers one row (server, name, address) while this app is signed in, and nothing else. No secret
 * leaves this app here. Exported behind the same signature permission, and the caller is checked.
 */
class HandoverAccountProvider : ContentProvider() {
    override fun onCreate() = true

    override fun query(uri: Uri, projection: Array<out String>?, selection: String?, selectionArgs: Array<out String>?,
                       sortOrder: String?): Cursor? {
        val ctx = context ?: return null
        if (uri.lastPathSegment != "account" || !Handover.callerAllowed(ctx.packageManager, callingPackage, ctx.packageName)) return null
        val app = ctx.applicationContext as? MailrApp ?: return null
        val s = app.graph.sessions.session.value
        return MatrixCursor(Handover.COLUMNS).apply { if (s != null) addRow(arrayOf(s.server, s.name, s.email)) }
    }

    override fun getType(uri: Uri): String? = null
    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?) = 0
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?) = 0
}
