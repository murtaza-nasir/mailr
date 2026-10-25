package io.github.murtazanasir.mailr.time

import java.time.Clock
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * The one source of "now" for both apps' screens, ViewModels, widgets and the data they show.
 *
 * In the apps it is the system clock in the device's current time zone, read afresh on every call,
 * so a change of time zone shows at once (the same as `ZonedDateTime.now()`).
 *
 * Tests pin the instant: the unit-test JVMs start with the system property `mailr.clock.now`
 * (android/build.gradle.kts files), the docs demo's Fri 2 Oct 2026 10:40 in America/Chicago, and
 * `user.timezone` set to that zone. A test can move it with [pin] and put it back with [reset].
 * The time zone always comes from the JVM default, so a test that sets another zone keeps working.
 *
 * Code outside this file reads the clock only through here; ClockSourceTest fails on a new direct
 * read (`System.currentTimeMillis()`, `Instant.now()`, `LocalDate.now()`, ...) unless the line says
 * why it needs the real clock with a `// clock-ok: <reason>` comment (logging, timeouts, file names).
 */
object AppClock {
    /** The system property that pins the instant in tests (an ISO offset date-time). */
    const val PROPERTY = "mailr.clock.now"

    private val initial: Instant? = System.getProperty(PROPERTY)?.takeIf { it.isNotBlank() }?.let {
        java.time.OffsetDateTime.parse(it).toInstant()
    }

    @Volatile private var fixed: Instant? = initial

    /** Pins "now" to [at] (tests). */
    fun pin(at: Instant) { fixed = at }

    /** Back to the instant the JVM started with (the pinned demo time in tests, the system clock in the apps). */
    fun reset() { fixed = initial }

    /** True while "now" is pinned (tests). */
    val pinned: Boolean get() = fixed != null

    /** The device's current time zone. */
    fun zone(): ZoneId = ZoneId.systemDefault()

    /** A java.time clock for [zone], for APIs that take one. */
    fun clock(zone: ZoneId = zone()): Clock = fixed?.let { Clock.fixed(it, zone) } ?: Clock.system(zone)

    fun millis(): Long = fixed?.toEpochMilli() ?: System.currentTimeMillis() // clock-ok: the clock itself

    fun instant(): Instant = fixed ?: Instant.now() // clock-ok: the clock itself

    fun now(zone: ZoneId = zone()): ZonedDateTime = ZonedDateTime.now(clock(zone))

    fun today(zone: ZoneId = zone()): LocalDate = LocalDate.now(clock(zone))

    fun localNow(zone: ZoneId = zone()): LocalDateTime = LocalDateTime.now(clock(zone))
}
