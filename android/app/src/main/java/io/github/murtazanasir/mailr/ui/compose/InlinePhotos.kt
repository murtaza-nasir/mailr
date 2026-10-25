package io.github.murtazanasir.mailr.ui.compose

import io.github.murtazanasir.mailr.util.catching

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import java.io.ByteArrayOutputStream
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/**
 * Photos in the compose body (ACT mail.insert_image @android.editor), with the web's rules
 * (frontend/src/features/compose/inlineImages.ts and useInlineImages.ts): a photo over
 * 2560 px on its longer side or over 3 MB is reduced to 2560 px (JPEG at quality 0.88; PNG and
 * WebP keep their type), unless the reduced copy would not be smaller. GIFs are never redrawn.
 * A new image shows at its own width, at most 600 px. Over 10 MB after this, it is refused.
 *
 * The phone adds one rule: a photo the server cannot send inline (HEIC from the camera, for
 * one) is redrawn as JPEG. The photo's EXIF rotation is applied whenever it is redrawn.
 */
object InlinePhotos {
    val INLINE_TYPES = setOf("image/png", "image/jpeg", "image/gif", "image/webp")
    const val MAX_INLINE_BYTES = 10 * 1024 * 1024
    const val DOWNSCALE_SIDE = 2560
    const val DOWNSCALE_BYTES = 3 * 1024 * 1024
    const val INSERT_MAX_WIDTH = 600
    const val QUALITY = 88

    data class Prepared(
        val bytes: ByteArray,
        val type: String,
        val filename: String,
        val width: Int,
        val height: Int,
        val reduced: Boolean,
    ) {
        /** The size the image gets in the message (initialSize on the web). */
        val shown: Pair<Int, Int> get() = initialSize(width, height)
    }

    class Refused(message: String) : Exception(message)

    fun shouldDownscale(width: Int, height: Int, size: Int, type: String): Boolean =
        type != "image/gif" && (maxOf(width, height) > DOWNSCALE_SIDE || size > DOWNSCALE_BYTES)

    fun downscaleSize(width: Int, height: Int, max: Int = DOWNSCALE_SIDE): Pair<Int, Int> {
        val long = maxOf(width, height)
        if (long <= max) return width to height
        val f = max.toDouble() / long
        return maxOf(1, Math.round(width * f).toInt()) to maxOf(1, Math.round(height * f).toInt())
    }

    fun heightFor(width: Int, natW: Int, natH: Int): Int =
        if (natW <= 0) width else maxOf(1, Math.round(width.toDouble() * natH / natW).toInt())

    fun initialSize(natW: Int, natH: Int): Pair<Int, Int> {
        val w = minOf(natW, INSERT_MAX_WIDTH)
        return w to heightFor(w, natW, natH)
    }

    /** The type the bytes really are (as the server sniffs them), else the given type. */
    fun sniff(bytes: ByteArray, fallback: String?): String? {
        fun at(i: Int) = bytes.getOrNull(i)?.toInt()?.and(0xff) ?: -1
        return when {
            at(0) == 0x89 && at(1) == 0x50 && at(2) == 0x4e && at(3) == 0x47 -> "image/png"
            at(0) == 0xff && at(1) == 0xd8 -> "image/jpeg"
            at(0) == 0x47 && at(1) == 0x49 && at(2) == 0x46 -> "image/gif"
            at(0) == 0x52 && at(1) == 0x49 && at(8) == 0x57 && at(9) == 0x45 && at(10) == 0x42 && at(11) == 0x50 -> "image/webp"
            else -> fallback?.lowercase()
        }
    }

    /** Check, reduce or convert a photo for the body. Throws [Refused] with a reason to show. */
    fun prepare(bytes: ByteArray, name: String, givenType: String?): Prepared {
        val type = sniff(bytes, givenType) ?: ""
        if (type == "image/svg+xml" || name.lowercase().let { it.endsWith(".svg") || it.endsWith(".svgz") }) {
            throw Refused(AppText.get(R.string.compose_image_svg, name))
        }
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) throw Refused(AppText.get(R.string.compose_image_unreadable, name))
        val rotation = rotationOf(bytes)
        val turned = rotation == 90 || rotation == 270
        val w = if (turned) bounds.outHeight else bounds.outWidth
        val h = if (turned) bounds.outWidth else bounds.outHeight
        val supported = type in INLINE_TYPES
        val out = if (!supported || shouldDownscale(w, h, bytes.size, type)) {
            val outType = if (type == "image/png" || type == "image/webp") type else "image/jpeg"
            val (tw, th) = downscaleSize(w, h)
            val redrawn = redraw(bytes, bounds.outWidth, bounds.outHeight, rotation, tw, th, outType)
                ?: throw Refused(AppText.get(R.string.compose_image_unreadable, name))
            if (supported && redrawn.size >= bytes.size) Prepared(bytes, type, name, w, h, reduced = false)
            else Prepared(redrawn, outType, renamed(name, outType), tw, th, reduced = supported)
        } else {
            Prepared(bytes, type, name, w, h, reduced = false)
        }
        if (out.bytes.size > MAX_INLINE_BYTES) throw Refused(AppText.get(R.string.compose_image_too_large, name))
        return out
    }

    /** A bitmap to show in the compose view: upright, at most [maxSide] on its longer side. */
    fun preview(bytes: ByteArray, maxSide: Int = 1600): Bitmap? = catching {
        val b = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, b)
        if (b.outWidth <= 0) return null
        var sample = 1
        while (maxOf(b.outWidth, b.outHeight) / (sample * 2) >= maxSide) sample *= 2
        val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
            ?: return null
        val rotation = rotationOf(bytes)
        if (rotation == 0) bmp
        else Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, Matrix().apply { postRotate(rotation.toFloat()) }, true)
    }.getOrNull()

    private fun rotationOf(bytes: ByteArray): Int = catching {
        when (ExifInterface(bytes.inputStream()).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)) {
            ExifInterface.ORIENTATION_ROTATE_90 -> 90
            ExifInterface.ORIENTATION_ROTATE_180 -> 180
            ExifInterface.ORIENTATION_ROTATE_270 -> 270
            else -> 0
        }
    }.getOrDefault(0)

    /** Decode at about the target size, rotate upright, scale exactly and encode. */
    private fun redraw(bytes: ByteArray, rawW: Int, rawH: Int, rotation: Int, tw: Int, th: Int, outType: String): ByteArray? {
        val turned = rotation == 90 || rotation == 270
        val needW = if (turned) th else tw
        val needH = if (turned) tw else th
        var sample = 1
        while (rawW / (sample * 2) >= needW && rawH / (sample * 2) >= needH) sample *= 2
        val decoded = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
            ?: return null
        val m = Matrix().apply {
            postScale(needW.toFloat() / decoded.width, needH.toFloat() / decoded.height)
            if (rotation != 0) postRotate(rotation.toFloat())
        }
        val drawn = Bitmap.createBitmap(decoded, 0, 0, decoded.width, decoded.height, m, true)
        val format = when (outType) {
            "image/png" -> Bitmap.CompressFormat.PNG
            "image/webp" -> if (android.os.Build.VERSION.SDK_INT >= 30) Bitmap.CompressFormat.WEBP_LOSSY
                else @Suppress("DEPRECATION") Bitmap.CompressFormat.WEBP
            else -> Bitmap.CompressFormat.JPEG
        }
        val buf = ByteArrayOutputStream()
        drawn.compress(format, QUALITY, buf)
        if (drawn !== decoded) drawn.recycle()
        decoded.recycle()
        return buf.toByteArray()
    }

    private fun renamed(name: String, type: String): String {
        val ext = if (type == "image/jpeg") "jpg" else type.substringAfter('/')
        val base = name.substringBeforeLast('.', name).ifBlank { "photo" }
        return "$base.$ext"
    }
}
