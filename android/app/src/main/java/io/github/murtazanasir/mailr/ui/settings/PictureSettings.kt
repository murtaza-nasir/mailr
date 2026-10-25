package io.github.murtazanasir.mailr.ui.settings

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import android.content.ActivityNotFoundException
import android.content.ContentResolver
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.media.ExifInterface
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.PhotoCamera
import androidx.compose.material.icons.outlined.PhotoLibrary
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import io.github.murtazanasir.mailr.look.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import io.github.murtazanasir.mailr.MailrApp
import io.github.murtazanasir.mailr.data.api.AccountPicture
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.removePicture
import io.github.murtazanasir.mailr.data.api.uploadPicture
import io.github.murtazanasir.mailr.data.api.useSignInPicture
import io.github.murtazanasir.mailr.data.repo.MailRepository
import io.github.murtazanasir.mailr.ui.components.Avatar
import io.github.murtazanasir.mailr.ui.components.PictureCache
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.io.File

/**
 * Settings → Your picture on the phone (the web's Settings → You → Your picture): the account
 * picture with Change (the system photo picker, or the camera), Remove and "Use my sign-in
 * picture". The publishing helpers (Gravatar, BIMI) stay on the web.
 */

/**
 * The phone's copy of a picture before upload: upright, at most [MAX_SIDE] px on its longer side,
 * drawn on white (a transparent PNG would turn black as JPEG) and encoded as a fresh JPEG. A fresh
 * encode carries no EXIF, so the photo's location and camera data never leave the phone. The
 * server re-encodes it once more (256 px PNG).
 */
object AccountPictureImage {
    const val MAX_SIDE = 1024
    const val QUALITY = 90
    /** More than this is not read at all (the camera's largest photos are well below). */
    const val MAX_INPUT = 40 * 1024 * 1024

    class Refused(message: String) : Exception(message)

    /** The size after reduction: the longer side at most [max]; never enlarged. */
    fun targetSize(width: Int, height: Int, max: Int = MAX_SIDE): Pair<Int, Int> {
        val long = maxOf(width, height)
        if (long <= max) return width to height
        val f = max.toDouble() / long
        return maxOf(1, Math.round(width * f).toInt()) to maxOf(1, Math.round(height * f).toInt())
    }

    /** Reduce and re-encode [bytes]. Throws [Refused] with a reason to show. */
    fun prepare(bytes: ByteArray): ByteArray {
        if (bytes.size > MAX_INPUT) throw Refused(AppText.get(R.string.settings_picture_too_large))
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) throw Refused(AppText.get(R.string.settings_picture_unreadable))
        val rotation = rotationOf(bytes)
        val turned = rotation == 90 || rotation == 270
        val (tw, th) = targetSize(if (turned) bounds.outHeight else bounds.outWidth, if (turned) bounds.outWidth else bounds.outHeight)
        // Decode at about the target size (powers of two), then scale exactly.
        val needW = if (turned) th else tw
        val needH = if (turned) tw else th
        var sample = 1
        while (bounds.outWidth / (sample * 2) >= needW && bounds.outHeight / (sample * 2) >= needH) sample *= 2
        val decoded = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
            ?: throw Refused(AppText.get(R.string.settings_picture_unreadable))
        val out = Bitmap.createBitmap(tw, th, Bitmap.Config.ARGB_8888)
        val m = Matrix().apply {
            postScale(needW.toFloat() / decoded.width, needH.toFloat() / decoded.height)
            if (rotation != 0) {
                postRotate(rotation.toFloat())
                when (rotation) {
                    90 -> postTranslate(tw.toFloat(), 0f)
                    180 -> postTranslate(tw.toFloat(), th.toFloat())
                    270 -> postTranslate(0f, th.toFloat())
                }
            }
        }
        Canvas(out).apply {
            drawColor(Color.WHITE)
            drawBitmap(decoded, m, Paint(Paint.FILTER_BITMAP_FLAG))
        }
        decoded.recycle()
        val buf = ByteArrayOutputStream()
        out.compress(Bitmap.CompressFormat.JPEG, QUALITY, buf)
        out.recycle()
        return buf.toByteArray()
    }

    private fun rotationOf(bytes: ByteArray): Int = catching {
        when (ExifInterface(bytes.inputStream()).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)) {
            ExifInterface.ORIENTATION_ROTATE_90 -> 90
            ExifInterface.ORIENTATION_ROTATE_180 -> 180
            ExifInterface.ORIENTATION_ROTATE_270 -> 270
            else -> 0
        }
    }.getOrDefault(0)
}

/** The three changes, each followed by a fresh GET /me so every avatar in the app follows. */
class AccountPictures(private val api: MailrApi, private val mail: MailRepository) {
    /** Reduce, re-encode and upload a picture (settings.picture_upload). */
    suspend fun upload(original: ByteArray): AccountPicture {
        val jpeg = withContext(Dispatchers.Default) { AccountPictureImage.prepare(original) }
        return api.uploadPicture(jpeg).also { refresh() }
    }

    /** settings.picture_remove: initials show again, and the sign-in picture stays away until asked for. */
    suspend fun remove(): AccountPicture = api.removePicture().also { refresh() }

    /** settings.picture_use_sign_in. */
    suspend fun useSignIn(): AccountPicture = api.useSignInPicture().also { refresh() }

    private suspend fun refresh() {
        catching { mail.refreshMe() }
    }

    /** Read a picked or photographed picture. Null when it cannot be read. */
    suspend fun read(resolver: ContentResolver, uri: Uri): ByteArray? = withContext(Dispatchers.IO) {
        catching {
            resolver.openInputStream(uri)?.use { input ->
                val buf = ByteArrayOutputStream()
                val chunk = ByteArray(64 * 1024)
                var total = 0
                while (true) {
                    val n = input.read(chunk)
                    if (n < 0) break
                    total += n
                    if (total > AccountPictureImage.MAX_INPUT) throw AccountPictureImage.Refused("too large")
                    buf.write(chunk, 0, n)
                }
                buf.toByteArray()
            }
        }.getOrNull()
    }
}

/** What the picture section shows. */
data class PictureUi(val picture: AccountPicture?, val busy: Boolean = false)

data class PictureCallbacks(
    val pick: () -> Unit = {},
    val take: () -> Unit = {},
    val remove: () -> Unit = {},
    val useSignIn: () -> Unit = {},
)

/** The line under the picture (the web's pictureSourceText). */
fun pictureSourceText(p: AccountPicture?): String = when {
    p?.has != true -> AppText.get(R.string.settings_picture_none)
    p.source == "idp" -> AppText.get(R.string.settings_picture_idp)
    else -> AppText.get(R.string.settings_picture_uploaded)
}

/** "Use my sign-in picture" shows when the provider sent one and the current picture is not it. */
fun offersSignInPicture(p: AccountPicture?): Boolean = p?.idpAvailable == true && p.source != "idp"

/** The API path of the account picture itself (not an address's own picture), or null. */
fun accountPicturePath(p: AccountPicture?): String? = p?.takeIf { it.has }?.let { "/api/v1/me/picture?v=${it.version}" }

/**
 * The system photo picker (no storage permission) and the camera (a shot in the app's cache,
 * res/xml/file_paths.xml "photos"; no camera permission: the camera app takes it).
 */
@Composable
fun rememberPictureSources(onPicked: (Uri) -> Unit, onNoCamera: () -> Unit): Pair<() -> Unit, () -> Unit> {
    val ctx = LocalContext.current
    var pending by rememberSaveable { mutableStateOf<String?>(null) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null) onPicked(uri)
    }
    val camera = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        val uri = pending
        pending = null
        if (ok && uri != null) onPicked(Uri.parse(uri))
    }
    val pick = { picker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) }
    val take = {
        val dir = File(ctx.cacheDir, "photos").apply { mkdirs() }
        dir.listFiles()?.filter { it.lastModified() < System.currentTimeMillis() - 86_400_000L }?.forEach { it.delete() }  // clock-ok: temp file cleanup
        val file = File(dir, "picture-${System.currentTimeMillis()}.jpg")  // clock-ok: file name
        val uri = FileProvider.getUriForFile(ctx, ctx.packageName + ".files", file)
        pending = uri.toString()
        try {
            camera.launch(uri)
        } catch (e: ActivityNotFoundException) {
            pending = null
            onNoCamera()
        }
    }
    return pick to take
}

/** Settings → Your picture. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun PictureSection(name: String, email: String, ui: PictureUi, cb: PictureCallbacks) {
    val pic = ui.picture
    var menu by remember { mutableStateOf(false) }
    Column(Modifier.padding(horizontal = 20.dp, vertical = 8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            AccountPictureImageView(name, email, pic)
            Spacer(Modifier.width(16.dp))
            Column(Modifier.weight(1f)) {
                Text(pictureSourceText(pic), style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.testTag("picture-source"))
                Spacer(Modifier.height(8.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Box {
                        FilledTonalButton(onClick = { menu = true }, enabled = !ui.busy) {
                            Text(stringResource(if (pic?.has == true) R.string.settings_picture_change else R.string.settings_picture_add))
                        }
                        DropdownMenu(menu, { menu = false }) {
                            DropdownMenuItem({ Text(stringResource(R.string.settings_picture_gallery)) }, { menu = false; cb.pick() },
                                leadingIcon = { Icon(Icons.Outlined.PhotoLibrary, null) })
                            DropdownMenuItem({ Text(stringResource(R.string.settings_picture_camera)) }, { menu = false; cb.take() },
                                leadingIcon = { Icon(Icons.Outlined.PhotoCamera, null) })
                        }
                    }
                    if (pic?.has == true) TextButton(onClick = cb.remove, enabled = !ui.busy) { Text(stringResource(R.string.settings_picture_remove)) }
                }
                if (offersSignInPicture(pic)) {
                    TextButton(onClick = cb.useSignIn, enabled = !ui.busy) { Text(stringResource(R.string.settings_picture_use_sign_in)) }
                }
            }
        }
        Text(stringResource(R.string.settings_picture_note),
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 8.dp))
    }
}

/** The account picture itself at 72 dp, over the initials (which show without one or while it loads). */
@Composable
private fun AccountPictureImageView(name: String, email: String, pic: AccountPicture?) {
    val app = LocalContext.current.applicationContext as? MailrApp
    val path = accountPicturePath(pic)
    val bmp by produceState<ImageBitmap?>(path?.let { PictureCache.peek(it) }, path) {
        if (path != null && app != null && value == null) value = withContext(Dispatchers.IO) { PictureCache.get(app, path) }
    }
    Box(Modifier.size(72.dp).testTag("account-picture")) {
        Avatar(name.ifBlank { null }, email, size = 72.dp)
        bmp?.let {
            Image(it, contentDescription = stringResource(R.string.settings_picture_desc), contentScale = ContentScale.Crop,
                modifier = Modifier.size(72.dp).clip(CircleShape))
        }
    }
}
