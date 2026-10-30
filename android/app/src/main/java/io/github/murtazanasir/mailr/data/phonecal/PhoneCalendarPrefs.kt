package io.github.murtazanasir.mailr.data.phonecal

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Settings → Calendar → Phone calendars, on this phone only: whether the feature is on, each
 * calendar's choice (Off, Show on this phone, Show and send busy times, Show and send details), and
 * what the server holds: per key sent, the calendar it came from, the snapshot's hash and when it
 * was sent. Plain SharedPreferences, so the calendar views and the worker read them without waiting.
 */
class PhoneCalendarPrefs(context: Context) {
    private val sp = context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    private val _enabled = MutableStateFlow(sp.getBoolean(K_ENABLED, false))
    val enabled: StateFlow<Boolean> = _enabled.asStateFlow()

    private val _modes = MutableStateFlow(readModes())
    /** Calendar id → choice; a calendar not listed is Off. */
    val modes: StateFlow<Map<Long, PhoneCalMode>> = _modes.asStateFlow()

    private val _deniedForever = MutableStateFlow(sp.getBoolean(K_DENIED, false))
    /** The user said no with "Don't ask again" (or twice): only the app's system settings can allow it now. */
    val deniedForever: StateFlow<Boolean> = _deniedForever.asStateFlow()

    fun setEnabled(on: Boolean) { _enabled.value = on; sp.edit().putBoolean(K_ENABLED, on).apply() }

    fun mode(id: Long): PhoneCalMode = _modes.value[id] ?: PhoneCalMode.OFF

    fun setMode(id: Long, mode: PhoneCalMode) {
        val m = _modes.value.toMutableMap()
        if (mode == PhoneCalMode.OFF) m.remove(id) else m[id] = mode
        _modes.value = m
        sp.edit().putString(K_MODES, m.entries.joinToString("\n") { "${it.key}=${it.value.name}" }).apply()
    }

    fun setDeniedForever(v: Boolean) { _deniedForever.value = v; sp.edit().putBoolean(K_DENIED, v).apply() }

    /** What the server holds for one key, as this phone last sent it. */
    data class Sent(val key: String, val calendarId: Long, val hash: String, val at: Long)

    fun sent(): Map<String, Sent> = sp.getString(K_SENT, "").orEmpty().split('\n').mapNotNull { l ->
        val p = l.split(' ')
        if (p.size != 4) null else {
            val id = p[1].toLongOrNull()
            val at = p[3].toLongOrNull()
            if (id == null || at == null) null else Sent(p[0], id, p[2], at)
        }
    }.associateBy { it.key }

    fun putSent(s: Sent) = writeSent(sent() + (s.key to s))
    fun removeSent(key: String) = writeSent(sent() - key)

    private fun writeSent(m: Map<String, Sent>) {
        sp.edit().putString(K_SENT, m.values.joinToString("\n") { "${it.key} ${it.calendarId} ${it.hash} ${it.at}" }).commit()
    }

    private fun readModes(): Map<Long, PhoneCalMode> = sp.getString(K_MODES, "").orEmpty().split('\n').mapNotNull { l ->
        val p = l.split('=', limit = 2)
        val id = p.getOrNull(0)?.toLongOrNull() ?: return@mapNotNull null
        val m = PhoneCalMode.entries.firstOrNull { it.name == p.getOrNull(1) } ?: return@mapNotNull null
        id to m
    }.toMap()

    companion object {
        const val FILE = "mailr_phone_calendars"
        private const val K_ENABLED = "enabled"
        private const val K_MODES = "modes"
        private const val K_DENIED = "denied_forever"
        private const val K_SENT = "sent"
    }
}
