package io.github.murtazanasir.mailr.data.share

import io.github.murtazanasir.mailr.util.catching

import okhttp3.Dns
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Interceptor
import java.io.IOException
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.net.UnknownHostException

/**
 * The phone's own guard for fetches of links someone else chose (the page title of a shared
 * link): names must resolve to public addresses only, and the connection is checked again
 * after it opens (an IP literal or a redirect skips DNS).
 */
object PublicDns : Dns {
    private val LOCAL_SUFFIXES = listOf(".lan", ".local", ".localdomain", ".internal", ".home", ".home.arpa", ".corp", ".intranet")

    override fun lookup(hostname: String): List<InetAddress> {
        if (!publicName(hostname)) throw UnknownHostException("$hostname is not a public host")
        val all = Dns.SYSTEM.lookup(hostname)
        if (all.isEmpty() || all.any { !isPublic(it) }) throw UnknownHostException("$hostname does not resolve to a public address")
        return all
    }

    /** An https link on the default port to a name that is not a LAN name or a private IP literal. */
    fun fetchable(url: String): Boolean {
        val u = url.trim().toHttpUrlOrNull() ?: return false
        if (!u.isHttps || u.port != 443 || u.username.isNotEmpty() || u.password.isNotEmpty()) return false
        if (!publicName(u.host)) return false
        literal(u.host)?.let { return isPublic(it) }
        return true
    }

    private fun publicName(host: String): Boolean {
        val h = host.lowercase().trimEnd('.')
        if (h.isEmpty() || h == "localhost" || '.' !in h && literal(h) == null) return false
        return LOCAL_SUFFIXES.none { h.endsWith(it) } && !h.endsWith(".localhost")
    }

    /** [host] as an address when it is an IP literal (no DNS lookup), else null. */
    private fun literal(host: String): InetAddress? {
        val h = host.removePrefix("[").removeSuffix("]")
        val looksIp = h.contains(':') || h.all { it.isDigit() || it == '.' }
        return if (looksIp) catching { InetAddress.getByName(h) }.getOrNull() else null
    }

    /** Public unicast: not loopback, private, link-local, CGNAT, multicast, unspecified or reserved. */
    fun isPublic(a: InetAddress): Boolean {
        if (a.isAnyLocalAddress || a.isLoopbackAddress || a.isLinkLocalAddress || a.isSiteLocalAddress || a.isMulticastAddress) return false
        val b = a.address
        return when (a) {
            is Inet4Address -> {
                val o0 = b[0].toInt() and 0xff
                val o1 = b[1].toInt() and 0xff
                !(o0 == 0 || o0 == 10 || o0 == 127 || o0 >= 224 ||
                    (o0 == 100 && o1 in 64..127) ||     // CGNAT 100.64.0.0/10
                    (o0 == 169 && o1 == 254) ||
                    (o0 == 172 && o1 in 16..31) ||
                    (o0 == 192 && o1 == 168) ||
                    (o0 == 192 && o1 == 0 && (b[2].toInt() and 0xff) in setOf(0, 2)) ||
                    (o0 == 198 && o1 in 18..19) ||
                    (o0 == 198 && o1 == 51 && (b[2].toInt() and 0xff) == 100) ||
                    (o0 == 203 && o1 == 0 && (b[2].toInt() and 0xff) == 113))
            }
            is Inet6Address -> {
                val f = b[0].toInt() and 0xff
                // fc00::/7 unique local.
                if (f and 0xfe == 0xfc) return false
                // 2001:db8::/32 documentation.
                if (u16(b, 0) == 0x2001 && u16(b, 2) == 0x0db8) return false
                // 64:ff9b:1::/48 (RFC 8215) is a site's own translator: never public.
                if (u16(b, 0) == 0x0064 && u16(b, 2) == 0xff9b && u16(b, 4) == 0x0001) return false
                // Forms that carry an IPv4 address are judged by every IPv4 address they carry.
                val inner = embeddedV4(b)
                if (inner != null) return inner.all { isPublic(InetAddress.getByAddress(it)) }
                true
            }
            else -> false
        }
    }

    private fun u16(b: ByteArray, i: Int) = ((b[i].toInt() and 0xff) shl 8) or (b[i + 1].toInt() and 0xff)

    /**
     * The IPv4 addresses inside an IPv6 address that only wraps them: IPv4-mapped (::ffff:0:0/96)
     * and IPv4-compatible (::/96), 6to4 (2002::/16, bits 16-47), NAT64 (64:ff9b::/96, last 32 bits)
     * and Teredo (2001::/32: the server at bits 32-63, the client in the last 32 bits inverted).
     * Null for an ordinary IPv6 address.
     */
    fun embeddedV4(b: ByteArray): List<ByteArray>? {
        if (b.size != 16) return null
        val first10Zero = (0 until 10).all { b[it].toInt() == 0 }
        if (first10Zero && u16(b, 10) == 0xffff) return listOf(b.copyOfRange(12, 16))
        if (first10Zero && u16(b, 10) == 0) return listOf(b.copyOfRange(12, 16))
        if (u16(b, 0) == 0x2002) return listOf(b.copyOfRange(2, 6))
        if (u16(b, 0) == 0x0064 && u16(b, 2) == 0xff9b && (4 until 12).all { b[it].toInt() == 0 }) return listOf(b.copyOfRange(12, 16))
        if (u16(b, 0) == 0x2001 && u16(b, 2) == 0x0000) {
            val client = ByteArray(4) { (b[12 + it].toInt() xor 0xff).toByte() }
            return listOf(b.copyOfRange(4, 8), client)
        }
        return null
    }

    /**
     * Where a redirect from [from] to [location] leads, when that hop is itself fetchable
     * (https, port 443, a public name or address); null otherwise. Each hop is checked, not only
     * the first link.
     */
    fun redirectTarget(from: String, location: String?): String? {
        if (location.isNullOrBlank()) return null
        val base = from.toHttpUrlOrNull() ?: return null
        val next = base.resolve(location.trim())?.toString() ?: return null
        return next.takeIf { fetchable(it) }
    }

    /** A network interceptor: refuses the request when the open connection goes to a non-public address. */
    val connectionGuard = Interceptor { chain ->
        val addr = chain.connection()?.route()?.socketAddress?.address
        if (addr != null && !isPublic(addr)) throw IOException("Refusing a non-public address")
        chain.proceed(chain.request())
    }
}
