package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.data.weather.OpenMeteo
import io.github.murtazanasir.mailr.data.weather.WeatherPlace
import io.github.murtazanasir.mailr.data.api.MailrJson
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

/** An answer with a field missing is a failed fetch or a skipped row, never a NullPointerException. */
class ServerAnswerNullsTest {
    @Test fun aForecastWithoutTodaysTemperaturesIsAFailedFetch() {
        val o = MailrJson.parseToJsonElement("""{"current":{"temperature_2m":12.5,"weather_code":3},"daily":{"temperature_2m_min":[4.0]}}""") as JsonObject
        try {
            OpenMeteo.parseForecast(o, WeatherPlace(41.8, -87.6, "Chicago"), 0)
            fail("a forecast without a high must fail")
        } catch (e: java.io.IOException) {
            // the last reading stays
        }
    }

    @Test fun aPlaceWithoutCoordinatesIsSkipped() {
        val o = MailrJson.parseToJsonElement("""{"results":[{"name":"Nowhere"},{"name":"Lisbon","admin1":"Lisbon","latitude":38.72,"longitude":-9.14}]}""") as JsonObject
        assertEquals(listOf("Lisbon"), OpenMeteo.parseSearch(o).map { it.name })
    }
}
