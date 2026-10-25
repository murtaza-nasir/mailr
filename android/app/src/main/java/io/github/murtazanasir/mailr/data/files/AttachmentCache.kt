package io.github.murtazanasir.mailr.data.files

import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.Attachment
import io.github.murtazanasir.mailr.data.api.MailrApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.io.InputStream

/**
 * Attachments downloaded for a viewer app, the share sheet or Save to Downloads, kept in the cache
 * folder (`attachment-files/<message>/<part>/<name>`): by part, so two attachments of one message
 * with the same name never share a file, and under the attachment's own name for the viewer.
 *
 * A download goes to `<name>.part` first. Only a complete one (every byte the server's
 * Content-Length promised, when it sent one) is renamed to `<name>`; a cut-off or failed download
 * deletes its `.part`. So a file under the attachment's name is always whole, and it is reused. The
 * folder is not the one of before 0.38 ("attachments"), where a cut-off download could stay under
 * the final name.
 */
class AttachmentCache(private val root: File, private val client: ApiClient, private val api: MailrApi) {
    /** The attachment's file, downloaded now when this phone does not have it whole. */
    suspend fun file(messageId: Long, a: Attachment): File = withContext(Dispatchers.IO) {
        val dir = File(root, "$FOLDER/$messageId/${a.part}").apply { mkdirs() }
        val f = File(dir, safeName(a.filename))
        if (f.isFile) return@withContext f
        val req = Request.Builder().url(api.attachmentUrl(messageId, a.part)).build()
        client.raw(req).use { r ->
            if (!r.isSuccessful) throw ApiException(r.code, "download failed")
            val body = r.body ?: throw IOException("empty answer")
            body.byteStream().use { input -> writeWhole(input, body.contentLength(), f) }
        }
        f
    }

    companion object {
        const val FOLDER = "attachment-files"

        /** A file name made of safe characters only, at most 120 of them. */
        fun safeName(name: String): String =
            name.replace(Regex("[^A-Za-z0-9._ -]"), "_").take(120).trim('.', ' ').ifBlank { "attachment" }

        /**
         * Copies [input] into [dest] through `dest.part`, checks the length against [expected] when it is
         * known (not negative), and renames on success. Any failure deletes the `.part` file and throws.
         */
        fun writeWhole(input: InputStream, expected: Long, dest: File): File {
            val part = File(dest.parentFile, dest.name + ".part")
            try {
                val n = part.outputStream().use { out -> input.copyTo(out) }
                if (expected >= 0 && n != expected) throw IOException("download incomplete: $n of $expected bytes")
                if (dest.exists()) dest.delete()
                if (!part.renameTo(dest)) throw IOException("could not keep the download")
                return dest
            } catch (e: Throwable) {
                part.delete()
                throw e
            }
        }
    }
}
