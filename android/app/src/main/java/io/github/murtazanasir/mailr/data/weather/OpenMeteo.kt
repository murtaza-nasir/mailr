package io.github.murtazanasir.mailr.data.weather

import io.github.murtazanasir.mailr.time.AppClock
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.double
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * Open-Meteo (open-meteo.com), asked straight from the phone: no key, no account, no cookies, no
 * mailr headers. Only the rounded coordinates (or the typed city name, for the search) leave the
 * phone. Weather never goes through the mailr server or a language model.
 */
class OpenMeteo(
    private val forecastBase: HttpUrl = "https://api.open-meteo.com/v1/forecast".toHttpUrl(),
    private val searchBase: HttpUrl = "https://geocoding-api.open-meteo.com/v1/search".toHttpUrl(),
    private val http: OkHttpClient = DEFAULT,
) {
    /** A city search result: "Springfield", "Illinois, United States". */
    data class City(val name: String, val region: String, val lat: Double, val lon: Double)

    /** Now, today's high and low, and the next day and a half hour by hour, in °C. */
    suspend fun forecast(p: WeatherPlace): WeatherReading = withContext(Dispatchers.IO) {
        val url = forecastBase.newBuilder()
            .addQueryParameter("latitude", "%.1f".format(Locale.ROOT, p.lat))
            .addQueryParameter("longitude", "%.1f".format(Locale.ROOT, p.lon))
            .addQueryParameter("current", "temperature_2m,weather_code,is_day")
            .addQueryParameter("daily", "temperature_2m_max,temperature_2m_min")
            .addQueryParameter("hourly", "temperature_2m,weather_code,is_day")
            .addQueryParameter("forecast_days", "2")
            .addQueryParameter("timeformat", "unixtime")
            .addQueryParameter("timezone", "auto")
            .build()
        val o = get(url)
        parseForecast(o, p, AppClock.millis())
    }

    /** Cities matching [name], in the phone's language. */
    suspend fun search(name: String): List<City> = withContext(Dispatchers.IO) {
        if (name.isBlank()) return@withContext emptyList()
        val url = searchBase.newBuilder().addQueryParameter("name", name.trim()).addQueryParameter("count", "8")
            .addQueryParameter("language", Locale.getDefault().language.ifEmpty { "en" }).addQueryParameter("format", "json").build()
        parseSearch(get(url))
    }

    private fun get(url: HttpUrl): JsonObject {
        http.newCall(Request.Builder().url(url).header("Accept", "application/json").build()).execute().use { r ->
            if (!r.isSuccessful) throw java.io.IOException("Open-Meteo answered ${r.code}")
            return Json.parseToJsonElement(r.body?.string().orEmpty()).jsonObject
        }
    }

    companion object {
        private val DEFAULT: OkHttpClient by lazy {
            OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS).build()
        }

        fun parseForecast(o: JsonObject, p: WeatherPlace, now: Long): WeatherReading {
            // A field missing from the answer is a failed fetch (the last reading stays), not a crash.
            fun JsonObject.need(k: String) = this[k] ?: throw java.io.IOException("forecast without $k")
            val cur = o.need("current").jsonObject
            val daily = o.need("daily").jsonObject
            return WeatherReading(
                cur.need("temperature_2m").jsonPrimitive.double,
                daily.need("temperature_2m_max").jsonArray.firstOrNull()?.jsonPrimitive?.double ?: throw java.io.IOException("forecast without a high"),
                daily.need("temperature_2m_min").jsonArray.firstOrNull()?.jsonPrimitive?.double ?: throw java.io.IOException("forecast without a low"),
                cur.need("weather_code").jsonPrimitive.int,
                cur["is_day"]?.let { isDay(it.jsonPrimitive) } ?: true,
                now, p, parseHours(o, now))
        }

        /** The hourly forecast from the hour under way, at most [MAX_HOURS] hours; none when the answer has no hourly part. */
        fun parseHours(o: JsonObject, now: Long): List<HourReading> {
            val h = o["hourly"]?.jsonObject ?: return emptyList()
            val times = h["time"]?.jsonArray ?: return emptyList()
            val temps = h["temperature_2m"]?.jsonArray ?: return emptyList()
            val codes = h["weather_code"]?.jsonArray ?: return emptyList()
            val days = h["is_day"]?.jsonArray
            return times.indices.mapNotNull { i ->
                val at = times[i].jsonPrimitive.longOrNull?.times(1000) ?: return@mapNotNull null
                val t = temps.getOrNull(i)?.jsonPrimitive?.doubleOrNull ?: return@mapNotNull null
                val c = codes.getOrNull(i)?.jsonPrimitive?.intOrNull ?: return@mapNotNull null
                HourReading(at, t, c, days?.getOrNull(i)?.let { isDay(it.jsonPrimitive) } ?: true)
            }.filter { it.at > now - 60 * 60_000 }.take(MAX_HOURS)
        }

        const val MAX_HOURS = 36

        private fun isDay(p: kotlinx.serialization.json.JsonPrimitive): Boolean = p.intOrNull?.let { n -> n == 1 } ?: p.booleanOrNull ?: true

        fun parseSearch(o: JsonObject): List<City> = o["results"]?.jsonArray.orEmpty().mapNotNull { e ->
            val r = e.jsonObject
            val name = r["name"]?.jsonPrimitive?.content ?: return@mapNotNull null
            val region = listOfNotNull(r["admin1"]?.jsonPrimitive?.content, r["country"]?.jsonPrimitive?.content)
                .filter { it.isNotBlank() && it != name }.joinToString(", ")
            City(name, region, r["latitude"]?.jsonPrimitive?.doubleOrNull ?: return@mapNotNull null,
                r["longitude"]?.jsonPrimitive?.doubleOrNull ?: return@mapNotNull null)
        }
    }
}
