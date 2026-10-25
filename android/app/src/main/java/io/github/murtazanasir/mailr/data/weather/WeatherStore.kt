package io.github.murtazanasir.mailr.data.weather

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import io.github.murtazanasir.mailr.ui.agenda.AgendaModel
import io.github.murtazanasir.mailr.ui.agenda.WeatherIcon
import java.util.Locale
import kotlin.math.roundToInt

/** Where the weather is for: this phone's approximate location, or a city picked in the widget's settings. */
enum class WeatherSource { LOCATION, CITY }

/** °C or °F; [AUTO] follows the phone's region (°F in the United States and a few others). */
enum class WeatherUnits { AUTO, CELSIUS, FAHRENHEIT;
    fun fahrenheit(locale: Locale = Locale.getDefault()): Boolean = when (this) {
        CELSIUS -> false
        FAHRENHEIT -> true
        AUTO -> locale.country in setOf("US", "LR", "MM", "BS", "BZ", "KY", "PW", "FM", "MH")
    }
}

/** A place: coordinates rounded to 0.1° (about 10 km), and its name when it was picked as a city. */
data class WeatherPlace(val lat: Double, val lon: Double, val name: String?) {
    companion object {
        fun rounded(lat: Double, lon: Double, name: String?) = WeatherPlace((lat * 10).roundToInt() / 10.0, (lon * 10).roundToInt() / 10.0, name)
    }
}

/** The last forecast fetched, in °C (converted when shown), with the hours after it. */
data class WeatherReading(val tempC: Double, val highC: Double, val lowC: Double, val code: Int, val day: Boolean, val fetchedAt: Long,
                          val place: WeatherPlace, val hours: List<HourReading> = emptyList())

/** One hour of the forecast: its start (epoch ms), °C, the WMO code, day or night. */
data class HourReading(val at: Long, val tempC: Double, val code: Int, val day: Boolean)

/**
 * The Agenda widget's choices on this phone and the last weather reading (plain SharedPreferences, so
 * the widget reads them without waiting). Nothing here goes to the mailr server.
 */
class WeatherStore(context: Context) {
    private val sp = context.applicationContext.getSharedPreferences("mailr_agenda_widget", Context.MODE_PRIVATE)

    var enabled: Boolean
        get() = sp.getBoolean(K_ENABLED, true)
        set(v) { sp.edit().putBoolean(K_ENABLED, v).apply() }

    var source: WeatherSource
        get() = catching { WeatherSource.valueOf(sp.getString(K_SOURCE, null) ?: "") }.getOrDefault(WeatherSource.LOCATION)
        set(v) { sp.edit().putString(K_SOURCE, v.name).apply() }

    var units: WeatherUnits
        get() = catching { WeatherUnits.valueOf(sp.getString(K_UNITS, null) ?: "") }.getOrDefault(WeatherUnits.AUTO)
        set(v) { sp.edit().putString(K_UNITS, v.name).apply() }

    /** The picked city (source CITY). */
    var city: WeatherPlace?
        get() = place(K_CITY)
        set(v) = putPlace(K_CITY, v)

    /** The phone's last approximate location (source LOCATION), already rounded. */
    var here: WeatherPlace?
        get() = place(K_HERE)
        set(v) { putPlace(K_HERE, v); sp.edit().putLong(K_HERE_AT, io.github.murtazanasir.mailr.time.AppClock.millis()).apply() }

    val hereAt: Long get() = sp.getLong(K_HERE_AT, 0)

    /** The person said no to approximate location: the settings offer a city and do not ask again on their own. */
    var locationDenied: Boolean
        get() = sp.getBoolean(K_DENIED, false)
        set(v) { sp.edit().putBoolean(K_DENIED, v).apply() }

    /** The weather app to open: a component "package/class", or null for the system's choice. */
    var app: String?
        get() = sp.getString(K_APP, null)
        set(v) { sp.edit().putString(K_APP, v).apply() }

    /** How many days the widget lists (it shows as many of them as fit). */
    var days: Int
        get() = sp.getInt(K_DAYS, AgendaModel.DEFAULT_DAYS).coerceIn(1, 14)
        set(v) { sp.edit().putInt(K_DAYS, v.coerceIn(1, 14)).apply() }

    /** The place the weather is for now, or null when none is known yet. */
    val place: WeatherPlace? get() = if (source == WeatherSource.CITY) city else here

    var reading: WeatherReading?
        get() {
            if (!sp.contains(K_R_AT)) return null
            val p = place(K_R_PLACE) ?: return null
            return WeatherReading(sp.getFloat(K_R_T, 0f).toDouble(), sp.getFloat(K_R_HI, 0f).toDouble(), sp.getFloat(K_R_LO, 0f).toDouble(),
                sp.getInt(K_R_CODE, 3), sp.getBoolean(K_R_DAY, true), sp.getLong(K_R_AT, 0), p, decodeHours(sp.getString(K_R_HOURS, null)))
        }
        set(r) {
            val e = sp.edit()
            if (r == null) listOf(K_R_T, K_R_HI, K_R_LO, K_R_CODE, K_R_DAY, K_R_AT, K_R_HOURS).forEach { e.remove(it) }
            else e.putFloat(K_R_T, r.tempC.toFloat()).putFloat(K_R_HI, r.highC.toFloat()).putFloat(K_R_LO, r.lowC.toFloat())
                .putInt(K_R_CODE, r.code).putBoolean(K_R_DAY, r.day).putLong(K_R_AT, r.fetchedAt).putString(K_R_HOURS, encodeHours(r.hours))
            e.apply()
            putPlace(K_R_PLACE, r?.place)
        }

    /**
     * What the widget shows: the reading for the current place, or null (weather off, no place, nothing
     * fetched yet, or a reading so old it would mislead).
     */
    fun shown(now: Long): AgendaModel.Weather? {
        if (!enabled) return null
        val r = reading ?: return null
        val p = place ?: return null
        if (kotlin.math.abs(r.place.lat - p.lat) > 0.25 || kotlin.math.abs(r.place.lon - p.lon) > 0.25) return null
        if (now - r.fetchedAt > STALE_MS) return null
        return show(r, units.fahrenheit(), if (source == WeatherSource.CITY) p.name else null)
    }

    private fun place(k: String): WeatherPlace? {
        if (!sp.contains("$k.lat")) return null
        return WeatherPlace(sp.getFloat("$k.lat", 0f).toDouble(), sp.getFloat("$k.lon", 0f).toDouble(), sp.getString("$k.name", null))
    }

    private fun putPlace(k: String, p: WeatherPlace?) {
        val e = sp.edit()
        if (p == null) e.remove("$k.lat").remove("$k.lon").remove("$k.name")
        else e.putFloat("$k.lat", p.lat.toFloat()).putFloat("$k.lon", p.lon.toFloat()).putString("$k.name", p.name)
        e.apply()
    }

    companion object {
        /** A reading older than this is not shown: six hours without a refresh (offline, or weather turned off and on). */
        const val STALE_MS = 6 * 60 * 60 * 1000L

        fun show(r: WeatherReading, fahrenheit: Boolean, place: String?): AgendaModel.Weather {
            fun t(c: Double) = (if (fahrenheit) c * 9 / 5 + 32 else c).roundToInt()
            return AgendaModel.Weather(WeatherIcon.of(r.code, r.day), t(r.tempC), t(r.highC), t(r.lowC), place,
                r.hours.map { AgendaModel.Hour(it.at, WeatherIcon.of(it.code, it.day), t(it.tempC)) })
        }

        /** "at,temp,code,day;…" (the hourly forecast in one preference). */
        fun encodeHours(hours: List<HourReading>): String =
            hours.joinToString(";") { "${it.at},${"%.1f".format(Locale.ROOT, it.tempC)},${it.code},${if (it.day) 1 else 0}" }

        fun decodeHours(s: String?): List<HourReading> = s.orEmpty().split(';').mapNotNull { part ->
            val f = part.split(',')
            if (f.size != 4) return@mapNotNull null
            HourReading(f[0].toLongOrNull() ?: return@mapNotNull null, f[1].toDoubleOrNull() ?: return@mapNotNull null,
                f[2].toIntOrNull() ?: return@mapNotNull null, f[3] == "1")
        }

        private const val K_ENABLED = "weather.enabled"
        private const val K_SOURCE = "weather.source"
        private const val K_UNITS = "weather.units"
        private const val K_CITY = "weather.city"
        private const val K_HERE = "weather.here"
        private const val K_HERE_AT = "weather.here.at"
        private const val K_APP = "weather.app"
        private const val K_DAYS = "agenda.days"
        private const val K_R_T = "weather.r.t"
        private const val K_R_HI = "weather.r.hi"
        private const val K_R_LO = "weather.r.lo"
        private const val K_R_CODE = "weather.r.code"
        private const val K_R_DAY = "weather.r.day"
        private const val K_R_AT = "weather.r.at"
        private const val K_R_PLACE = "weather.r.place"
        private const val K_R_HOURS = "weather.r.hours"
        private const val K_DENIED = "weather.location.denied"
    }
}
