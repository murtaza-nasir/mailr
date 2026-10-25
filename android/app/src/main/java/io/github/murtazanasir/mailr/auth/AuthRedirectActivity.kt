package io.github.murtazanasir.mailr.auth

import android.app.Activity
import android.os.Bundle
import io.github.murtazanasir.mailr.data.auth.SignInRedirect

/**
 * The sign-in provider's answer (io.github.murtazanasir.mailr:/oauth2redirect), in place of AppAuth's own
 * RedirectUriReceiverActivity: forwarded to the sign-in waiting in MainActivity's task only when it
 * answers that sign-in (session-core SignInRedirect).
 */
class AuthRedirectActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        SignInRedirect.forward(this, intent?.data, "io.github.murtazanasir.mailr.ui.MainActivity")
        finishAndRemoveTask()
    }
}
