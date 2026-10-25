package io.github.murtazanasir.mailr.data.files

import io.github.murtazanasir.mailr.util.catching

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.provider.MediaStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException

/**
 * Save to Downloads: a copy of a downloaded attachment in the phone's Downloads folder, through
 * MediaStore (Android 10 and later need no storage permission for it). The entry stays pending (hidden
 * from other apps) until the copy is whole; a failed copy removes it.
 */
object Downloads {
    suspend fun save(context: Context, src: File, name: String, mime: String): Uri = withContext(Dispatchers.IO) {
        val resolver = context.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, name)
            put(MediaStore.MediaColumns.MIME_TYPE, mime.ifBlank { "application/octet-stream" })
            put(MediaStore.MediaColumns.RELATIVE_PATH, android.os.Environment.DIRECTORY_DOWNLOADS)
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: throw IOException("Downloads refused the file")
        try {
            val out = resolver.openOutputStream(uri) ?: throw IOException("Downloads refused the file")
            out.use { o -> src.inputStream().use { it.copyTo(o) } }
            resolver.update(uri, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }, null, null)
            uri
        } catch (e: Throwable) {
            catching { resolver.delete(uri, null, null) }
            throw e
        }
    }
}
