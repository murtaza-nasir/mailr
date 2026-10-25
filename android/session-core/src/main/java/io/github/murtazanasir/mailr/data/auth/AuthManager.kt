package io.github.murtazanasir.mailr.data.auth

import io.github.murtazanasir.mailr.session.R
import io.github.murtazanasir.mailr.i18n.AppText
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.browser.customtabs.CustomTabsIntent
import io.github.murtazanasir.mailr.data.api.AuthApi
import io.github.murtazanasir.mailr.data.api.NativeConfig
import io.github.murtazanasir.mailr.data.api.SessionResponse
import kotlinx.coroutines.suspendCancellableCoroutine
import net.openid.appauth.AuthorizationException
import net.openid.appauth.AuthorizationRequest
import net.openid.appauth.AuthorizationResponse
import net.openid.appauth.AuthorizationService
import net.openid.appauth.AuthorizationServiceConfiguration
import net.openid.appauth.ResponseTypeValues
import net.openid.appauth.TokenResponse
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * Sign-in with the instance's OIDC provider (e.g. Pocket ID) using AppAuth:
 * authorization code + PKCE in a Custom Tab, then the ID token is exchanged with mailr for a
 * device session. mailr never sees the provider's password, and the app never stores the
 * provider's tokens: only the mailr session is kept (encrypted, see [SessionStore]).
 */
class AuthManager(private val context: Context, private val api: AuthApi, private val sessions: SessionStore,
                  /** Retry an unconfirmed sign-out once there is a network (LogoutWork). */
                  private val retryLogout: () -> Unit = {},
                  /** How the server lists this device session ("Pixel 9", "Notes on Pixel 9"). */
                  private val deviceName: () -> String = { defaultDeviceName() }) {
    private val service by lazy { AuthorizationService(context.applicationContext) }
    private val pending by lazy { PendingAuthorization(context) }

    /**
     * The household invite the app was set up from (mailr://setup?…&invite=…) for a server, sent with
     * the single sign-on to that server only.
     */
    @Volatile var invite: (server: String) -> String? = { null }

    suspend fun nativeConfig(): NativeConfig = api.nativeConfig()

    /** The older servers' sign-in settings (no native sign-in): whether a password works. */
    suspend fun authConfig() = api.authConfig()

    suspend fun authorizationIntent(cfg: NativeConfig, toolbarColor: Int? = null): Intent {
        val sc = fetchConfiguration(Uri.parse(cfg.issuer))
        // The nonce binds the provider's ID token to the server this sign-in is for.
        val origin = NonceBinding.canonicalOrigin(sessions.server)
        val seed = NonceBinding.newSeed()
        val req = AuthorizationRequest.Builder(sc, cfg.clientId, ResponseTypeValues.CODE, Uri.parse(cfg.redirectUri))
            .setScopes(cfg.scopes.ifEmpty { listOf("openid", "email", "profile") })
            .setPrompt(null)
            .setNonce(NonceBinding.nonce(origin, seed))
            .build()
        pending.begin(PendingAuthorization.Pending(req.state!!, seed, origin, cfg.redirectUri,
            System.currentTimeMillis()))  // clock-ok: the sign-in's age, across process restarts
        val tab = CustomTabsIntent.Builder().setShowTitle(true).setUrlBarHidingEnabled(true)
        toolbarColor?.let {
            tab.setDefaultColorSchemeParams(
                androidx.browser.customtabs.CustomTabColorSchemeParams.Builder().setToolbarColor(it).build())
        }
        return service.getAuthorizationRequestIntent(req, tab.build())
    }

    /** Finish the flow with the Custom Tab's result intent. */
    suspend fun complete(data: Intent?): SessionResponse {
        val started = pending.current()
        pending.clear()
        val resp = data?.let { AuthorizationResponse.fromIntent(it) }
        val err = data?.let { AuthorizationException.fromIntent(it) }
        if (resp == null) throw IllegalStateException(failure(err))
        // The answer belongs to the sign-in this app started, for the server it is still set to.
        val origin = NonceBinding.canonicalOrigin(sessions.server)
        if (started == null || resp.state != started.state || resp.request.nonce != NonceBinding.nonce(started.origin, started.seed)) {
            throw IllegalStateException(AppText.get(R.string.session_sign_in_failed))
        }
        if (started.origin != origin) throw IllegalStateException(AppText.get(R.string.session_sign_in_server_changed))
        val tokens = exchange(resp)
        val idToken = tokens.idToken ?: throw IllegalStateException(AppText.get(R.string.session_sign_in_unfinished))
        val s = api.nativeSignIn(idToken, deviceName().take(90), invite(sessions.server), started.seed)
        sessions.save(s.sessionToken ?: error(AppText.get(R.string.session_sign_in_unfinished)), s.csrf.orEmpty(), s.user.email, s.user.name)
        return s
    }

    /**
     * What the sign-in screen says when the provider's answer is an error: a fixed text per
     * kind, never the answer's own description, which anything that can open the redirect could write.
     */
    private fun failure(err: AuthorizationException?): String = when {
        err == null -> AppText.get(R.string.session_sign_in_cancelled)
        err.code == AuthorizationException.GeneralErrors.USER_CANCELED_AUTH_FLOW.code -> AppText.get(R.string.session_sign_in_cancelled)
        err.error == "access_denied" -> AppText.get(R.string.session_sign_in_refused)
        else -> AppText.get(R.string.session_sign_in_failed)
    }

    /**
     * "Continue as" in the Notes app: the mailr app handed over [token] for [server]; with the
     * [verifier] whose challenge it carried, the server opens this app's own device session.
     */
    suspend fun redeemHandover(server: String, token: String, verifier: String): SessionResponse {
        sessions.server = server
        val s = api.redeemHandover(token, verifier, deviceName().take(90))
        sessions.save(s.sessionToken ?: error(AppText.get(R.string.session_sign_in_unfinished)), s.csrf.orEmpty(), s.user.email, s.user.name)
        return s
    }

    suspend fun passwordSignIn(email: String, password: String): SessionResponse {
        val s = api.passwordSignIn(email, password)
        sessions.save(s.sessionToken ?: error(AppText.get(R.string.session_server_too_old)), s.csrf.orEmpty(),
            s.user.email, s.user.name)
        return s
    }

    /**
     * End the session on the server, then forget it here. When the server cannot be reached the
     * session is kept aside and ended later ([LogoutWork]), so it does not stay valid. Returns
     * false in that case.
     */
    suspend fun signOut(): Boolean {
        val s = sessions.session.value
        val r = api.signOut()
        val gone = r.isSuccess || (r.exceptionOrNull() as? io.github.murtazanasir.mailr.data.api.ApiException)?.code in setOf(401, 403)
        if (!gone && s != null) {
            sessions.savePendingLogout(s)
            retryLogout()
        }
        sessions.clear()
        return gone
    }

    private suspend fun fetchConfiguration(issuer: Uri): AuthorizationServiceConfiguration =
        suspendCancellableCoroutine { cont ->
            AuthorizationServiceConfiguration.fetchFromIssuer(issuer) { cfg, ex ->
                if (cfg != null) cont.resume(cfg)
                else cont.resumeWithException(IllegalStateException(
                    AppText.get(R.string.session_sign_in_service_unreachable,
                        ex?.errorDescription ?: ex?.message ?: AppText.get(R.string.session_unknown_error))))
            }
        }

    private suspend fun exchange(resp: AuthorizationResponse): TokenResponse = suspendCancellableCoroutine { cont ->
        service.performTokenRequest(resp.createTokenExchangeRequest()) { tokens, _ ->
            if (tokens != null) cont.resume(tokens)
            // A fixed text: the provider's own description is protocol talk ("invalid_grant").
            else cont.resumeWithException(IllegalStateException(AppText.get(R.string.session_sign_in_unfinished)))
        }
    }

    companion object {
        fun defaultDeviceName(): String = "${Build.MANUFACTURER} ${Build.MODEL}".trim().take(90)
    }

    fun dispose() = service.dispose()
}
