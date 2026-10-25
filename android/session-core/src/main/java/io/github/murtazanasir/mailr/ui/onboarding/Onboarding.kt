package io.github.murtazanasir.mailr.ui.onboarding

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.session.R
import io.github.murtazanasir.mailr.ui.components.refuseObscuredTouches
import androidx.compose.ui.res.stringResource
import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Dns
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material.icons.outlined.VisibilityOff
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedButton
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.NativeConfig

data class OnboardingState(
    val server: String,
    val config: NativeConfig? = null,
    val checking: Boolean = false,
    val signingIn: Boolean = false,
    val error: String? = null,
    /** The mailr app's account on this phone (the Notes app's "Continue as"), or null. */
    val account: HandoverAccount? = null,
    /** A server from a setup link, waiting for the person to confirm it. */
    val setupOffer: String? = null,
) {
    /** "Continue as" shows while the server field is empty or names the mailr app's server. */
    val continueAs: HandoverAccount?
        get() = account?.takeIf { server.isBlank() || io.github.murtazanasir.mailr.data.auth.SessionStore.normalizeServer(server) == it.server }
}

/** The account another app on this phone is signed in with: server, name and address, no secret. */
data class HandoverAccount(val server: String, val name: String, val email: String)

/**
 * The app the screen signs in to: its name, a line under it and its monochrome icon (a drawable),
 * so the mailr app and the Notes app share the screen and each looks like itself.
 */
data class OnboardingBrand(
    val name: String,
    val tagline: String,
    @androidx.annotation.DrawableRes val icon: Int? = null,
)

data class OnboardingCallbacks(
    val setServer: (String) -> Unit,
    val connect: () -> Unit,
    val changeServer: () -> Unit,
    val signInOidc: () -> Unit,
    val signInPassword: (String, String) -> Unit,
    /** "Continue as <name>" (the Notes app with the mailr app signed in on the same server). */
    val continueAs: () -> Unit = {},
    /** Use the setup link's server, or keep the screen as it was. */
    val confirmSetup: () -> Unit = {},
    val dismissSetup: () -> Unit = {},
)

@Composable
fun OnboardingScreen(s: OnboardingState, cb: OnboardingCallbacks, brand: OnboardingBrand) {
    val bg = Brush.verticalGradient(listOf(MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.55f), MaterialTheme.colorScheme.surface))
    Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.surface).background(bg)) {
        Column(
            Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding().verticalScroll(rememberScrollState())
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Surface(shape = RoundedCornerShape(28.dp), color = MaterialTheme.colorScheme.primary, modifier = Modifier.size(88.dp)) {
                if (brand.icon != null) Icon(painterResource(brand.icon), null, tint = MaterialTheme.colorScheme.onPrimary)
            }
            Spacer(Modifier.height(20.dp))
            Text(brand.name, style = MaterialTheme.typography.displaySmall, fontWeight = FontWeight.Medium)
            Text(brand.tagline, style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(32.dp))
            Surface(shape = RoundedCornerShape(28.dp), color = MaterialTheme.colorScheme.surfaceContainerLow, tonalElevation = 1.dp,
                modifier = Modifier.widthIn(max = 460.dp).fillMaxWidth()) {
                val step = when { s.setupOffer != null -> 0; s.config == null -> 1; else -> 2 }
                AnimatedContent(step, label = "step") { st -> // i18n-ignore: animation label
                    Column(Modifier.padding(24.dp)) {
                        when (st) {
                            0 -> SetupLinkStep(s, cb, brand)
                            1 -> ServerStep(s, cb)
                            else -> SignInStep(s, cb)
                        }
                    }
                }
            }
        }
    }
}

/**
 * "Continue as <name>": one tap signs this app in through the mailr app on the phone, then the
 * other ways below an "or".
 */
@Composable
private fun ContinueAs(a: HandoverAccount, s: OnboardingState, cb: OnboardingCallbacks) {
    Button(onClick = cb.continueAs, enabled = !s.signingIn && !s.checking,
        modifier = Modifier.fillMaxWidth().height(52.dp).refuseObscuredTouches().testTag("continue-as")) {
        if (s.signingIn) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
        else Text(stringResource(R.string.session_continue_as, a.name.ifBlank { a.email }), maxLines = 1)
    }
    Spacer(Modifier.height(6.dp))
    Text(stringResource(R.string.session_continue_as_detail, a.server.removePrefix("https://")), style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
    Spacer(Modifier.height(16.dp))
    Box(contentAlignment = Alignment.Center, modifier = Modifier.fillMaxWidth()) {
        HorizontalDivider()
        Surface(color = MaterialTheme.colorScheme.surfaceContainerLow) {
            Text("  ${stringResource(R.string.session_or)}  ", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    Spacer(Modifier.height(12.dp))
}

/**
 * A setup link named a server: the full address, in plain words, before anything is sent
 * to it. A link from a mail or a web page can name any server, so the person decides.
 */
@Composable
private fun androidx.compose.foundation.layout.ColumnScope.SetupLinkStep(s: OnboardingState, cb: OnboardingCallbacks, brand: OnboardingBrand) {
    val offer = s.setupOffer ?: return
    Text(stringResource(R.string.session_setup_link_title), style = MaterialTheme.typography.titleLarge)
    Spacer(Modifier.height(8.dp))
    Text(stringResource(R.string.session_setup_link_body, brand.name), style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant)
    Spacer(Modifier.height(12.dp))
    Text(offer.removePrefix("https://"), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.fillMaxWidth().testTag("setup-link-host"))
    if (offer.startsWith("http://")) {
        Spacer(Modifier.height(8.dp))
        Text(stringResource(R.string.session_plain_http_warning), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
    }
    Spacer(Modifier.height(12.dp))
    Text(stringResource(R.string.session_setup_link_warning), style = MaterialTheme.typography.bodyMedium)
    Spacer(Modifier.height(20.dp))
    // An overlay over this button could make the person use a server they never saw (tapjacking).
    Button(onClick = cb.confirmSetup, modifier = Modifier.fillMaxWidth().height(52.dp).refuseObscuredTouches().testTag("setup-link-use")) {
        Text(stringResource(R.string.session_setup_link_use))
    }
    Spacer(Modifier.height(8.dp))
    TextButton(onClick = cb.dismissSetup, modifier = Modifier.align(Alignment.CenterHorizontally)) {
        Text(stringResource(R.string.session_setup_link_cancel))
    }
}

@Composable
private fun ServerStep(s: OnboardingState, cb: OnboardingCallbacks) {
    s.continueAs?.let { ContinueAs(it, s, cb) }
    Text(stringResource(R.string.session_server_title), style = MaterialTheme.typography.titleLarge)
    Spacer(Modifier.height(4.dp))
    Text(stringResource(R.string.session_server_hint), style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant)
    Spacer(Modifier.height(20.dp))
    OutlinedTextField(
        s.server, cb.setServer, label = { Text(stringResource(R.string.session_server_address)) }, singleLine = true,
        leadingIcon = { Icon(Icons.Outlined.Dns, null) },
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go, autoCorrectEnabled = false),
        keyboardActions = KeyboardActions(onGo = { cb.connect() }),
        isError = s.error != null, supportingText = s.error?.let { { Text(it) } },
        modifier = Modifier.fillMaxWidth(),
    )
    Spacer(Modifier.height(16.dp))
    Button(onClick = cb.connect, enabled = !s.checking && s.server.isNotBlank(), modifier = Modifier.fillMaxWidth().height(52.dp)) {
        if (s.checking) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp) else Text(stringResource(R.string.session_continue))
    }
}

@Composable
private fun androidx.compose.foundation.layout.ColumnScope.SignInStep(s: OnboardingState, cb: OnboardingCallbacks) {
    val cfg = s.config!!
    var email by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var show by rememberSaveable { mutableStateOf(false) }
    Text(stringResource(R.string.session_sign_in_title), style = MaterialTheme.typography.titleLarge)
    Text(s.server.removePrefix("https://"), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    if (s.server.startsWith("http://")) {
        Spacer(Modifier.height(8.dp))
        Text(stringResource(R.string.session_plain_http_warning), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
    }
    Spacer(Modifier.height(20.dp))
    s.continueAs?.let { ContinueAs(it, s, cb) }
    if (cfg.enabled) {
        Button(onClick = cb.signInOidc, enabled = !s.signingIn, modifier = Modifier.fillMaxWidth().height(52.dp)) {
            if (s.signingIn) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
            else { Icon(Icons.Outlined.Lock, null); Spacer(Modifier.size(8.dp)); Text(signInLabel(cfg.providerName)) }
        }
        if (cfg.localLogin) {
            Spacer(Modifier.height(20.dp))
            Box(contentAlignment = Alignment.Center, modifier = Modifier.fillMaxWidth()) {
                HorizontalDivider()
                Surface(color = MaterialTheme.colorScheme.surfaceContainerLow) {
                    Text("  ${stringResource(R.string.session_or)}  ", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Spacer(Modifier.height(12.dp))
        }
    }
    if (cfg.localLogin) {
        OutlinedTextField(email, { email = it }, label = { Text(stringResource(R.string.session_email)) }, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email, imeAction = ImeAction.Next, autoCorrectEnabled = false),
            modifier = Modifier.fillMaxWidth())
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(password, { password = it }, label = { Text(stringResource(R.string.session_password)) }, singleLine = true,
            visualTransformation = if (show) VisualTransformation.None else PasswordVisualTransformation(),
            trailingIcon = { IconButton(onClick = { show = !show }) {
                Icon(if (show) Icons.Outlined.VisibilityOff else Icons.Outlined.Visibility, stringResource(if (show) R.string.session_hide_password else R.string.session_show_password)) } },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { if (email.isNotBlank() && password.isNotBlank()) cb.signInPassword(email, password) }),
            modifier = Modifier.fillMaxWidth())
        Spacer(Modifier.height(16.dp))
        val primary = !cfg.enabled
        val onClick = { cb.signInPassword(email, password) }
        if (primary) Button(onClick = onClick, enabled = !s.signingIn && email.isNotBlank() && password.isNotBlank(),
            modifier = Modifier.fillMaxWidth().height(52.dp).refuseObscuredTouches()) { Text(stringResource(R.string.session_sign_in)) }
        else OutlinedButton(onClick = onClick, enabled = !s.signingIn && email.isNotBlank() && password.isNotBlank(),
            modifier = Modifier.fillMaxWidth().height(52.dp).refuseObscuredTouches()) { Text(stringResource(R.string.session_sign_in_with_password)) }
    }
    if (s.error != null) {
        Spacer(Modifier.height(12.dp))
        Text(s.error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth())
    }
    Spacer(Modifier.height(8.dp))
    TextButton(onClick = cb.changeServer, modifier = Modifier.align(Alignment.CenterHorizontally)) { Text(stringResource(R.string.session_use_different_server)) }
}

/** "Sign in with Pocket ID" when the server names its provider, else "Sign in with single sign-on":
 *  a name guessed from the issuer's host read as "Id" or "Sso". */
@Composable
fun signInLabel(providerName: String): String =
    if (providerName.isBlank()) stringResource(R.string.session_sign_in_sso)
    else stringResource(R.string.session_sign_in_with, providerName.trim())

/**
 * The server address from a setup link, `mailr://setup?server=https%3A%2F%2Fmail.example.com`
 * (the web setup guide's Get the app page opens it after the app is installed; the Notes app's is
 * `mailr-notes://setup?server=…`, [scheme] "mailr-notes"), or null when the
 * link is not one: another scheme or host, no server, not http(s), credentials or a path in it.
 * The address only fills in the sign-in screen, where it is shown before anything is sent.
 */
fun parseSetupServer(link: String?, scheme: String = "mailr"): String? {
    if (link.isNullOrBlank() || link.length > 600) return null
    val uri = catching { java.net.URI(link) }.getOrNull() ?: return null
    if (!uri.scheme.equals(scheme, ignoreCase = true) || !uri.host.equals("setup", ignoreCase = true)) return null
    val raw = (uri.rawQuery ?: return null).split('&').firstNotNullOfOrNull {
        val (k, v) = it.split('=', limit = 2).let { p -> p[0] to p.getOrElse(1) { "" } }
        if (k == "server") catching { java.net.URLDecoder.decode(v, "UTF-8") }.getOrNull() else null
    }?.trim() ?: return null
    val server = catching { java.net.URI(raw) }.getOrNull() ?: return null
    if (server.scheme?.lowercase() !in setOf("https", "http") || server.host.isNullOrBlank()) return null
    if (server.rawUserInfo != null || (server.rawPath ?: "").trim('/').isNotEmpty() || server.rawQuery != null || server.rawFragment != null) return null
    return "${server.scheme.lowercase()}://${server.host.lowercase()}${if (server.port > 0) ":${server.port}" else ""}"
}

/**
 * The household invite in a setup link (mailr://setup?server=…&invite=…, Admin → Users → Invite
 * someone; backend core/invites.py), or null. Only the token's own characters are taken.
 */
fun parseSetupInvite(link: String?, scheme: String = "mailr"): String? {
    if (parseSetupServer(link, scheme) == null) return null
    val q = catching { java.net.URI(link!!).rawQuery }.getOrNull() ?: return null
    return q.split('&').firstNotNullOfOrNull {
        val (k, v) = it.split('=', limit = 2).let { p -> p[0] to p.getOrElse(1) { "" } }
        if (k == "invite") v.takeIf { t -> Regex("[A-Za-z0-9_\\-]{20,100}").matches(t) } else null
    }
}
