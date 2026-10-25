package io.github.murtazanasir.mailr.data.auth

import android.content.pm.PackageManager
import android.util.Base64
import java.security.MessageDigest
import java.security.SecureRandom

/**
 * "Continue as <name>": the Notes app signs in through the mailr app on the same phone.
 *
 * 1. The Notes app reads the mailr app's account (server, name, address; no secret) from the mailr
 *    app's provider and offers "Continue as <name>" when the servers match.
 * 2. On the tap it makes a random [newVerifier] and starts the mailr app's HandoverActivity for a
 *    result, with the verifier's [challengeOf] and its device name.
 * 3. The mailr app checks the caller (the Notes app of its own build, signed with the same
 *    certificate), asks the server for a single-use token bound to the challenge
 *    (`POST /auth/handover`, its own device session) and returns it with the server address.
 * 4. The Notes app redeems the token with the verifier (`POST /auth/handover/redeem`) for a device
 *    session of its own.
 *
 * The provider and the activity are protected by a signature permission that both apps declare
 * ([permission]), and the mailr app checks the calling package and its certificate again.
 */
object Handover {
    const val EXTRA_CHALLENGE = "challenge"
    const val EXTRA_DEVICE = "device_name"
    const val EXTRA_TOKEN = "token"
    const val EXTRA_SERVER = "server"
    const val EXTRA_ERROR = "error"
    const val ACTIVITY = "io.github.murtazanasir.mailr.auth.HandoverActivity"
    const val APP_NOTES = "notes"
    val COLUMNS = arrayOf("server", "name", "email")

    /** The mailr app of the same build as the Notes app [notesPackage] (a debug build pairs with a debug build). */
    fun mailrPackageFor(notesPackage: String): String = notesPackage.replaceFirst(".notes", "")

    /** The Notes app of the same build as the mailr app [mailrPackage]. */
    fun notesPackageFor(mailrPackage: String): String =
        if (mailrPackage.endsWith(".debug")) mailrPackage.removeSuffix(".debug") + ".notes.debug" else "$mailrPackage.notes"

    fun permission(mailrPackage: String) = "$mailrPackage.permission.SIGN_IN_HANDOVER"

    fun authority(mailrPackage: String) = "$mailrPackage.handover"

    /** A PKCE verifier: 32 random bytes, base64url without padding (43 characters). */
    fun newVerifier(random: SecureRandom = SecureRandom()): String =
        b64(ByteArray(32).also { random.nextBytes(it) })

    /** PKCE's S256: base64url(sha256(verifier)). */
    fun challengeOf(verifier: String): String = b64(MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray()))

    fun isChallenge(s: String?): Boolean = s != null && Regex("^[A-Za-z0-9_-]{43}$").matches(s)

    /**
     * The mailr app's check of who asked: the Notes app of this build, signed with this app's
     * certificate. The signature permission already keeps other apps out; this is the second lock.
     */
    fun callerAllowed(pm: PackageManager, caller: String?, ownPackage: String): Boolean =
        caller != null && caller == notesPackageFor(ownPackage) &&
            pm.checkSignatures(caller, ownPackage) == PackageManager.SIGNATURE_MATCH

    private fun b64(bytes: ByteArray): String = Base64.encodeToString(bytes, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
}
