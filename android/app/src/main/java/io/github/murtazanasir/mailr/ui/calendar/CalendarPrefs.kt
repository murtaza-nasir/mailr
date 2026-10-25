package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import android.provider.Settings
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import io.github.murtazanasir.mailr.data.cal.Zones

/**
 * The Calendar screen's own choices on this phone: the hour height (pinch zoom), tasks on the
 * calendar, the travel-banner answer per phone zone, the home zone, whether visibility switches
 * apply only to this phone, and recent searches. Kept in plain SharedPreferences so the screen
 * reads them without waiting.
 */
class CalendarPrefs(context: Context) {
    private val sp = context.applicationContext.getSharedPreferences("mailr_calendar", Context.MODE_PRIVATE)

    private val _hourHeight = MutableStateFlow(sp.getFloat(K_HOUR, DEFAULT_HOUR).coerceIn(MIN_HOUR, MAX_HOUR))
    /** dp per hour in Day, 3-day, Work week and Week. */
    val hourHeight: StateFlow<Float> = _hourHeight.asStateFlow()

    private val _showTasks = MutableStateFlow(sp.getBoolean(K_TASKS, false))
    val showTasks: StateFlow<Boolean> = _showTasks.asStateFlow()

    private val _deviceOnly = MutableStateFlow(sp.getBoolean(K_DEVICE_ONLY, false))
    /** Visibility switches change only this phone (calendar.toggle_visibility, "Only on this phone"). */
    val deviceOnly: StateFlow<Boolean> = _deviceOnly.asStateFlow()

    private val _homeZone = MutableStateFlow(Zones.iana(sp.getString(K_HOME, null)))
    /** The account's zone (profile time zone); null until known. */
    val homeZone: StateFlow<String?> = _homeZone.asStateFlow()

    private val _travel = MutableStateFlow(readTravel())
    /** The travel banner's answer per phone zone: "phone" or "home". */
    val travel: StateFlow<Map<String, String>> = _travel.asStateFlow()

    private val _recent = MutableStateFlow(sp.getString(K_RECENT, "").orEmpty().split('\n').filter { it.isNotBlank() })
    val recent: StateFlow<List<String>> = _recent.asStateFlow()

    fun setHourHeight(dp: Float) {
        val v = dp.coerceIn(MIN_HOUR, MAX_HOUR)
        _hourHeight.value = v
        sp.edit().putFloat(K_HOUR, v).apply()
    }

    fun setShowTasks(on: Boolean) { _showTasks.value = on; sp.edit().putBoolean(K_TASKS, on).apply() }
    fun setDeviceOnly(on: Boolean) { _deviceOnly.value = on; sp.edit().putBoolean(K_DEVICE_ONLY, on).apply() }

    fun setHomeZone(name: String?) {
        // Kept as an IANA id (a Windows name from an older server maps; an unknown one is ignored).
        val id = if (name == null) null else Zones.iana(name) ?: return
        _homeZone.value = id
        sp.edit().putString(K_HOME, id).apply()
    }

    /** Remember the travel banner's answer for [phoneZone]; null forgets it (the banner shows again). */
    fun setTravel(phoneZone: String, choice: String?) {
        val m = _travel.value.toMutableMap()
        if (choice == null) m.remove(phoneZone) else m[phoneZone] = choice
        _travel.value = m
        sp.edit().putString(K_TRAVEL, m.entries.joinToString("\n") { "${it.key}=${it.value}" }).apply()
    }

    fun addRecent(q: String) {
        val t = q.trim()
        if (t.isEmpty()) return
        val l = (listOf(t) + _recent.value.filterNot { it.equals(t, ignoreCase = true) }).take(MAX_RECENT)
        _recent.value = l
        sp.edit().putString(K_RECENT, l.joinToString("\n")).apply()
    }

    fun clearRecent() { _recent.value = emptyList(); sp.edit().remove(K_RECENT).apply() }

    private fun readTravel(): Map<String, String> = sp.getString(K_TRAVEL, "").orEmpty().split('\n')
        .mapNotNull { l -> l.split('=', limit = 2).takeIf { it.size == 2 }?.let { it[0] to it[1] } }.toMap()

    companion object {
        const val DEFAULT_HOUR = 56f
        const val MIN_HOUR = 32f
        const val MAX_HOUR = 128f
        const val MAX_RECENT = 8
        private const val K_HOUR = "hour_height"
        private const val K_TASKS = "show_tasks"
        private const val K_DEVICE_ONLY = "visibility_device_only"
        private const val K_HOME = "home_zone"
        private const val K_TRAVEL = "travel_choice"
        private const val K_RECENT = "recent_searches"

        @Volatile private var instance: CalendarPrefs? = null
        fun of(context: Context): CalendarPrefs = instance ?: synchronized(this) {
            instance ?: CalendarPrefs(context).also { instance = it }
        }

        /** For tests: forget the shared instance (each test gets a fresh application). */
        fun reset() { instance = null }
    }
}

/** Animations off in the system settings: transitions jump instead of sliding. */
fun reducedMotion(context: Context): Boolean =
    catching { Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f }.getOrDefault(false)
