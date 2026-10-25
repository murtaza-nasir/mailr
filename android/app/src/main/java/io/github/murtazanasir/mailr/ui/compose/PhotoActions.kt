package io.github.murtazanasir.mailr.ui.compose

import android.content.ActivityNotFoundException
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.FileProvider
import java.io.File
import io.github.murtazanasir.mailr.R

/** "Take photo" and "Choose photos" for the composer (ACT mail.insert_image @android.editor). The system photo
 *  picker and the camera app need no storage or camera permission. */
data class PhotoActions(val take: () -> Unit, val pick: () -> Unit)

@Composable
fun rememberPhotoActions(vm: ComposeViewModel): PhotoActions {
    val ctx = LocalContext.current
    var pending by rememberSaveable { mutableStateOf<String?>(null) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.PickMultipleVisualMedia()) { uris ->
        vm.insertPhotos(ctx.contentResolver, uris)
    }
    val camera = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        val uri = pending
        pending = null
        if (ok && uri != null) vm.insertPhotos(ctx.contentResolver, listOf(Uri.parse(uri)))
    }
    return PhotoActions(
        take = {
            // Camera shots land in the app's cache (res/xml/file_paths.xml); old ones are cleared.
            val dir = File(ctx.cacheDir, "photos").apply { mkdirs() }
            dir.listFiles()?.filter { it.lastModified() < System.currentTimeMillis() - 86_400_000L }?.forEach { it.delete() }  // clock-ok: temp file cleanup
            val file = File(dir, "photo-${System.currentTimeMillis()}.jpg")  // clock-ok: file name
            val uri = FileProvider.getUriForFile(ctx, ctx.packageName + ".files", file)
            pending = uri.toString()
            try {
                camera.launch(uri)
            } catch (e: ActivityNotFoundException) {
                pending = null
                vm.notify(ctx.getString(R.string.compose_no_camera))
            }
        },
        pick = { picker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) },
    )
}
