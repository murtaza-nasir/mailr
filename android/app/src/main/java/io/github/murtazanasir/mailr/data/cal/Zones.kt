package io.github.murtazanasir.mailr.data.cal

import io.github.murtazanasir.mailr.util.catching

import java.time.ZoneId
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.KSerializer
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.descriptors.nullable
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder

/**
 * Zone names as java.time takes them (9 Oct 2026). Outlook writes Windows names ("Central
 * Standard Time"), on which ZoneId.of throws; the server sends IANA ids only, and an older server
 * or a stored row may still hold a Windows name. Every zone the app reads goes through here:
 * an IANA id stays, a Windows name maps (WindowsZones.kt, the server's table), anything else is
 * null and the caller's zone applies.
 */
object Zones {
    /** The IANA id for a zone name (IANA, Windows, UTC, a prefixed id), or null when unknown. */
    fun iana(name: String?): String? {
        val n = name?.trim()?.trim('"').orEmpty()
        if (n.isEmpty()) return null
        if (n.uppercase() in setOf("UTC", "Z", "GMT", "ETC/UTC")) return "UTC"
        WINDOWS_ZONES[n]?.let { return it }
        parse(n)?.let { return it.id }
        // "/mozilla.org/20050126_1/Europe/London" and similar prefixed ids.
        val parts = n.split('/')
        for (i in 1 until parts.size - 1) parse(parts.drop(i).joinToString("/"))?.let { return it.id }
        return null
    }

    /** The zone a name stands for, or null (never throws). */
    fun of(name: String?): ZoneId? = iana(name)?.let { parse(it) }

    /** The zone a name stands for, else [fallback]. */
    fun orDefault(name: String?, fallback: ZoneId): ZoneId = of(name) ?: fallback

    // Region ids only ("Europe/London", "UTC"): offsets such as "UTC-05:00" are no zone a
    // browser or the server takes, so they read as unknown.
    private fun parse(id: String): ZoneId? =
        if (id != "UTC" && '/' !in id) null else catching { ZoneId.of(id) }.getOrNull()
}

/** A zone field decoded through [Zones.iana]: an IANA id or null, never a name ZoneId.of refuses. */
@OptIn(ExperimentalSerializationApi::class)
object IanaZoneSerializer : KSerializer<String?> {
    override val descriptor: SerialDescriptor = PrimitiveSerialDescriptor("io.github.murtazanasir.mailr.IanaZone", PrimitiveKind.STRING).nullable
    override fun deserialize(decoder: Decoder): String? =
        if (decoder.decodeNotNullMark()) Zones.iana(decoder.decodeString()) else decoder.decodeNull()
    override fun serialize(encoder: Encoder, value: String?) {
        if (value == null) encoder.encodeNull() else encoder.encodeString(value)
    }
}
