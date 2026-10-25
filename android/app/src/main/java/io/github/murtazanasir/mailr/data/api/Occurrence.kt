package io.github.murtazanasir.mailr.data.api

import io.github.murtazanasir.mailr.util.catching

import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

/**
 * The canonical occurrence string: a timed occurrence is the UTC
 * instant as Python's `isoformat()` writes it (`2026-10-02T15:00:00+00:00`), an all-day one is
 * the date (`2026-10-02`). Reminder keys, citations and event row keys are built from it.
 *
 * The server sends it as `occurrence`; [canonical] exists for the older forms still found in
 * stored reminder keys and in answers from servers before the calendar build
 * (`20261002T150000Z`, `2026-10-02T11:00:00-04:00`). Two canonical strings are compared as
 * strings, never parsed.
 */
object Occurrence {
    private val SECONDS = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss")
    private val COMPACT = Regex("^(\\d{4})(\\d{2})(\\d{2})T(\\d{2})(\\d{2})(\\d{2})(Z?)$")
    private val COMPACT_DATE = Regex("^(\\d{4})(\\d{2})(\\d{2})$")

    /** Python `datetime.isoformat()` of a UTC instant: microseconds only when there are any. */
    fun utc(i: Instant): String {
        val t = i.atOffset(ZoneOffset.UTC)
        val micros = t.nano / 1000
        return t.format(SECONDS) + (if (micros != 0) ".%06d".format(micros) else "") + "+00:00"
    }

    /**
     * Any form a server or an older build produced, in canonical form. A time without an
     * offset is read in [zone] (the server reads it in the user's zone). Unreadable input comes
     * back unchanged, and null or blank becomes "" (a task without a due).
     */
    fun canonical(s: String?, zone: ZoneId = ZoneId.systemDefault()): String {
        val v = s?.trim().orEmpty()
        if (v.isEmpty()) return ""
        if (v.length == 10 && v[4] == '-' && v[7] == '-') return v
        COMPACT_DATE.matchEntire(v)?.let { m -> return "${m.groupValues[1]}-${m.groupValues[2]}-${m.groupValues[3]}" }
        COMPACT.matchEntire(v)?.let { m ->
            val g = m.groupValues
            val local = LocalDateTime.of(g[1].toInt(), g[2].toInt(), g[3].toInt(), g[4].toInt(), g[5].toInt(), g[6].toInt())
            return utc(if (g[7] == "Z") local.toInstant(ZoneOffset.UTC) else local.atZone(zone).toInstant())
        }
        catching { return utc(OffsetDateTime.parse(v).toInstant()) }
        catching { return utc(Instant.parse(v)) }
        catching { return utc(LocalDateTime.parse(v).atZone(zone).toInstant()) }
        return v
    }

    fun isDate(s: String): Boolean = s.length == 10

    /**
     * Where an occurrence string falls in time, for ordering only ("this and the ones after"):
     * the instant, or the start of the date in [zone]. Null when it cannot be read.
     */
    fun epochMs(s: String?, zone: ZoneId = ZoneId.systemDefault()): Long? {
        val v = canonical(s, zone).ifEmpty { return null }
        if (isDate(v)) return catching { java.time.LocalDate.parse(v).atStartOfDay(zone).toInstant().toEpochMilli() }.getOrNull()
        return catching { OffsetDateTime.parse(v).toInstant().toEpochMilli() }.getOrNull()
    }
}

/** Reminder keys: `e|<uid>|<occurrence>|<minutes>` and `t|<uid>|<due>|<minutes>`. */
object ReminderKeys {
    fun event(uid: String, occurrence: String, minutes: Int) = "e|$uid|$occurrence|$minutes"
    fun task(uid: String, due: String, minutes: Int) = "t|$uid|$due|$minutes"

    /**
     * A key in canonical form. Keys stored before the calendar build carry whatever string the
     * server returned then (an offset, or the compact iCalendar form); normalising them keeps a
     * reminder the user already handled from firing again under its new key. A UID may hold
     * "|", so the occurrence is the second field from the end.
     */
    fun normalize(key: String, zone: ZoneId = ZoneId.systemDefault()): String {
        val parts = key.split('|')
        if (parts.size < 4 || (parts[0] != "e" && parts[0] != "t")) return key
        val occ = parts[parts.size - 2]
        val canon = Occurrence.canonical(occ, zone)
        if (canon == occ) return key
        return (parts.subList(0, parts.size - 2) + canon + parts.last()).joinToString("|")
    }
}
