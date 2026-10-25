package io.github.murtazanasir.mailr.ui.onboarding

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.session.R
import io.github.murtazanasir.mailr.i18n.AppText
import android.content.Intent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.graphics.toArgb
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import io.github.murtazanasir.mailr.data.api.NativeConfig
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.auth.AuthManager
import io.github.murtazanasir.mailr.data.auth.SessionStore
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import io.github.murtazanasir.mailr.data.auth.Handover

/**
 * Where "Continue as" asks (the Notes app: the mailr app on the phone, session-core Handover):
 * the account it is signed in with, and the intent that asks it for a hand-over.
 */
interface HandoverSource {
    /** Read off the main thread (a provider in another app). */
    fun account(): HandoverAccount?
    fun intent(challenge: String, device: String): Intent
}

/**
 * The server step and the sign-in step (OIDC with PKCE, or a password), for either app; with a
 * [handover], also "Continue as <name>".
 */
class OnboardingViewModel(private val sessions: SessionStore, private val auth: AuthManager,
                          private val handover: HandoverSource? = null,
                          private val deviceName: () -> String = { AuthManager.defaultDeviceName() }) : ViewModel() {
    private val _state = MutableStateFlow(OnboardingState(server = sessions.server))
    val state: StateFlow<OnboardingState> = _state.asStateFlow()

    /** The intent that asks the mailr app for a hand-over. */
    private val _handoverLaunch = MutableSharedFlow<Intent>(extraBufferCapacity = 1)
    val handoverLaunch: SharedFlow<Intent> = _handoverLaunch
    private var verifier: String? = null
    private var handoverServer: String? = null

    /** Reads the mailr app's account again (on start and whenever the screen comes back). */
    fun refreshHandover() {
        val h = handover ?: return
        viewModelScope.launch {
            val a = withContext(Dispatchers.IO) { catching { h.account() }.getOrNull() }
            _state.update { it.copy(account = a) }
        }
    }

    fun continueAs() {
        val h = handover ?: return
        val a = _state.value.continueAs ?: return
        val v = Handover.newVerifier()
        verifier = v
        handoverServer = a.server
        _state.update { it.copy(signingIn = true, error = null) }
        _handoverLaunch.tryEmit(h.intent(Handover.challengeOf(v), deviceName().take(90)))
    }

    /** The mailr app's answer: a token for its server, or why not. */
    fun finishHandover(ok: Boolean, data: Intent?) {
        val v = verifier
        verifier = null
        val token = data?.getStringExtra(Handover.EXTRA_TOKEN)
        val server = data?.getStringExtra(Handover.EXTRA_SERVER)?.let { SessionStore.normalizeServer(it) }
        if (!ok || v == null || token == null || server == null || server != handoverServer) {
            _state.update { it.copy(signingIn = false, error = data?.getStringExtra(Handover.EXTRA_ERROR) ?: AppText.get(R.string.session_handover_cancelled)) }
            refreshHandover()
            return
        }
        viewModelScope.launch {
            try {
                auth.redeemHandover(server, token, v)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(signingIn = false, error = e.userMessage()) }
            }
        }
    }

    /** The Custom Tab intent to launch for OIDC sign-in. */
    private val _launch = MutableSharedFlow<Intent>(extraBufferCapacity = 1)
    val launch: SharedFlow<Intent> = _launch

    fun setServer(v: String) = _state.update { it.copy(server = v, error = null) }

    fun connect() {
        val server = SessionStore.normalizeServer(_state.value.server)
        _state.update { it.copy(server = server, checking = true, error = null) }
        viewModelScope.launch {
            sessions.server = server
            try {
                val cfg = auth.nativeConfig()
                _state.update { it.copy(config = cfg, checking = false) }
            } catch (e: Exception) {
                // Older servers without native sign-in still allow password sign-in.
                val fallback = catching { auth.authConfig() }.getOrNull()
                if (fallback != null) {
                    _state.update { it.copy(checking = false,
                        config = NativeConfig(enabled = false, localLogin = fallback.localLogin)) }
                } else {
                    _state.update { it.copy(checking = false, error = AppText.get(R.string.session_server_unreachable, e.userMessage())) }
                }
            }
        }
    }

    fun changeServer() = _state.update { it.copy(config = null, error = null) }

    /**
     * A setup link (mailr://setup?server=…) names a server. Nothing changes yet: the screen
     * asks first, with the full address, and only [confirmSetup] uses it. A link for the server
     * already in use asks nothing.
     */
    fun offerSetup(server: String) {
        val s = SessionStore.normalizeServer(server)
        if (s.isBlank() || (_state.value.config != null && _state.value.server == s)) return
        _state.update { it.copy(setupOffer = s) }
    }

    /** The person confirmed the setup link's server: fill it in and check it. */
    fun confirmSetup() {
        val s = _state.value.setupOffer ?: return
        _state.update { it.copy(setupOffer = null, server = s, config = null, error = null) }
        connect()
    }

    /** The person declined the setup link: the server step stays as it was. */
    fun dismissSetup() = _state.update { it.copy(setupOffer = null) }

    fun startOidc(toolbarColor: Int) {
        val cfg = _state.value.config ?: return
        _state.update { it.copy(signingIn = true, error = null) }
        viewModelScope.launch {
            try {
                _launch.emit(auth.authorizationIntent(cfg, toolbarColor))
            } catch (e: Exception) {
                _state.update { it.copy(signingIn = false, error = e.message ?: e.userMessage()) }
            }
        }
    }

    fun finishOidc(data: Intent?) {
        viewModelScope.launch {
            try {
                auth.complete(data)
            } catch (e: Exception) {
                _state.update { it.copy(signingIn = false, error = (e as? IllegalStateException)?.message ?: e.userMessage()) }
            }
        }
    }

    fun password(email: String, password: String) {
        _state.update { it.copy(signingIn = true, error = null) }
        viewModelScope.launch {
            try {
                auth.passwordSignIn(email.trim(), password)
            } catch (e: Exception) {
                _state.update { it.copy(signingIn = false, error = e.userMessage()) }
            }
        }
    }
}

/**
 * The sign-in screen with its OIDC launcher: [setup] is a server from a setup link (offered for
 * confirmation, never used without it), cleared with [consumeSetup].
 */
@Composable
fun OnboardingRoute(vm: OnboardingViewModel, setup: String?, consumeSetup: () -> Unit, brand: OnboardingBrand) {
    val s by vm.state.collectAsStateWithLifecycle()
    LaunchedEffect(setup) { setup?.let { vm.offerSetup(it); consumeSetup() } }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { vm.finishOidc(it.data) }
    LaunchedEffect(Unit) { vm.launch.collect { launcher.launch(it) } }
    val handover = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        vm.finishHandover(it.resultCode == android.app.Activity.RESULT_OK, it.data)
    }
    LaunchedEffect(Unit) {
        vm.handoverLaunch.collect { i ->
            try {
                handover.launch(i)
            } catch (e: android.content.ActivityNotFoundException) {
                vm.finishHandover(false, null)
            } catch (e: SecurityException) {
                vm.finishHandover(false, null)
            }
        }
    }
    // The mailr app may have signed in or out meanwhile.
    androidx.lifecycle.compose.LifecycleResumeEffect(Unit) {
        vm.refreshHandover()
        onPauseOrDispose { }
    }
    val toolbar = MaterialTheme.colorScheme.surface.toArgb()
    OnboardingScreen(s, OnboardingCallbacks(vm::setServer, vm::connect, vm::changeServer, { vm.startOidc(toolbar) }, vm::password,
        vm::continueAs, vm::confirmSetup, vm::dismissSetup), brand)
}
