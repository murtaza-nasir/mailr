package io.github.murtazanasir.mailr.notesapp.auth

import io.github.murtazanasir.mailr.util.catching

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import io.github.murtazanasir.mailr.data.auth.Handover
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.ui.onboarding.HandoverAccount
import io.github.murtazanasir.mailr.ui.onboarding.HandoverSource

/**
 * The mailr app of this build on the phone, for "Continue as <name>" (session-core Handover): its
 * account from its provider, and the intent that asks it for a hand-over. Both are behind the
 * signature permission the two apps share, and the mailr app must be signed with this app's
 * certificate; without the mailr app, or signed out, there is no account.
 */
class MailrHandover(context: Context) : HandoverSource {
    private val ctx = context.applicationContext
    private val mailr = Handover.mailrPackageFor(ctx.packageName)

    /**
     * The mailr app is ours: signed with this app's certificate. Without this check an app that took
     * the mailr app's package name on a phone without mailr could offer its own server.
     */
    fun trusted(): Boolean = catching {
        ctx.packageManager.checkSignatures(mailr, ctx.packageName) == PackageManager.SIGNATURE_MATCH
    }.getOrDefault(false)

    override fun account(): HandoverAccount? = if (!trusted()) null else try {
        ctx.contentResolver.query(Uri.parse("content://${Handover.authority(mailr)}/account"), null, null, null, null)?.use { c ->
            if (!c.moveToFirst()) null
            else HandoverAccount(SessionStore.normalizeServer(c.getString(0).orEmpty()), c.getString(1).orEmpty(), c.getString(2).orEmpty())
                .takeIf { it.server.isNotBlank() }
        }
    } catch (e: SecurityException) {
        null // A mailr app signed with another certificate (or one without the permission).
    } catch (e: IllegalArgumentException) {
        null
    }

    override fun intent(challenge: String, device: String): Intent = Intent()
        .setComponent(ComponentName(mailr, Handover.ACTIVITY))
        .putExtra(Handover.EXTRA_CHALLENGE, challenge)
        .putExtra(Handover.EXTRA_DEVICE, device)
}
