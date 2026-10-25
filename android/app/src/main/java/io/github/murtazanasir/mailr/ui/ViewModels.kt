package io.github.murtazanasir.mailr.ui

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.data.api.mailAccounts

import android.content.Intent
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.Person
import io.github.murtazanasir.mailr.data.api.SessionInfo
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.ui.mail.UiMessage
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

class SearchViewModel(private val g: AppGraph) : ViewModel() {
    private val _people = MutableStateFlow<List<Person>>(emptyList())
    val people: StateFlow<List<Person>> = _people.asStateFlow()
    private var job: Job? = null

    fun query(q: String) {
        job?.cancel()
        val term = q.split(' ').lastOrNull()?.takeIf { ':' !in it }.orEmpty()
        if (term.length < 2) {
            _people.value = emptyList()
            return
        }
        job = viewModelScope.launch {
            delay(200)
            _people.value = g.compose.suggest(term).take(4)
        }
    }

    fun remember(q: String) = viewModelScope.launch { g.settings.rememberSearch(q) }
}

class SettingsViewModel(private val g: AppGraph) : ViewModel() {
    private val _sessions = MutableStateFlow<List<SessionInfo>?>(null)
    val sessions: StateFlow<List<SessionInfo>?> = _sessions.asStateFlow()

    private val _accounts = MutableStateFlow<List<io.github.murtazanasir.mailr.data.api.MailAccount>?>(null)
    /** The mail accounts with their health; null while loading, empty when unreadable. */
    val accounts: StateFlow<List<io.github.murtazanasir.mailr.data.api.MailAccount>?> = _accounts.asStateFlow()

    init {
        viewModelScope.launch { _sessions.value = catching { g.api.sessions() }.getOrDefault(emptyList()) }
        refreshAccounts()
    }

    fun refreshAccounts() {
        viewModelScope.launch {
            _accounts.value = try {
                g.api.mailAccounts()
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (_: Exception) {
                _accounts.value.orEmpty()
            }
        }
    }

    fun revoke(s: SessionInfo) {
        viewModelScope.launch {
            catching { g.api.revokeSession(s.id) }
            _sessions.value = catching { g.api.sessions() }.getOrDefault(_sessions.value.orEmpty())
        }
    }

    fun signOut() {
        // The app scope: the screen goes away with the session, the wipe must still finish.
        g.scope.launch {
            catching { g.recorder.stop() }
            // While the session still works: delete this phone's push target, leave the push app.
            catching { g.push.signOut() }
            g.auth.signOut()
            g.wipeLocal(signOut = true)
        }
    }
}
