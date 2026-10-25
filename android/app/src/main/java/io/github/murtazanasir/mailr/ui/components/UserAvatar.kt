package io.github.murtazanasir.mailr.ui.components

import io.github.murtazanasir.mailr.util.catching

import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.MailrApp
import io.github.murtazanasir.mailr.data.api.Me
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withContext

/** Which picture shows for you (docs: user-guide "Your picture"). Pure, so it is unit-tested. */
object OwnPicture {
    /**
     * The API path of your picture for [email] (your default address when null): that address's
     * own picture, else your account picture, else null (initials). An address that is not one of
     * yours gets null unless [mine] says the message is yours anyway.
     */
    fun path(me: Me?, email: String?, mine: Boolean = false): String? {
        if (me == null) return null
        val ids = me.identities
        val ident = if (email == null) ids.firstOrNull { it.isDefault } ?: ids.firstOrNull()
        else ids.firstOrNull { it.email.equals(email.trim(), ignoreCase = true) }
        if (email != null && ident == null && !mine) return null
        if (ident?.hasAvatar == true) return "/api/v1/identities/${ident.id}/avatar"
        val pic = me.picture
        if (pic?.has == true) return "/api/v1/me/picture?v=${pic.version}"
        return null
    }
}

/** Your pictures, by API path. Cleared on sign-out ([MailrApp] wipeLocal), so the next account never sees them. */
object PictureCache {
    // Counted in bytes, not entries: one large picture weighed as much as 24 small ones.
    private val cache = object : LruCache<String, ImageBitmap>(8 * 1024 * 1024) {
        override fun sizeOf(key: String, value: ImageBitmap): Int = value.width * value.height * 4
    }

    fun clear() = cache.evictAll()

    internal fun putForTest(path: String, bmp: ImageBitmap) { cache.put(path, bmp) }

    internal fun cachedForTest(path: String): ImageBitmap? = cache.get(path)

    /** The cached picture for [path], without fetching. */
    fun peek(path: String): ImageBitmap? = cache.get(path)

    fun get(app: MailrApp, path: String): ImageBitmap? {
        cache.get(path)?.let { return it }
        val bytes = catching { app.graph.api.proxiedImage(path) }.getOrNull()?.second ?: return null
        val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap() ?: return null
        cache.put(path, bmp)
        return bmp
    }
}

/**
 * You (or a message you sent): your picture over the initials avatar, fetched with the session
 * (GET identities/{id}/avatar or me/picture). Without a picture, or before it loads, the
 * initials show.
 */
@Composable
fun UserAvatar(name: String?, email: String, size: Dp = 40.dp, modifier: Modifier = Modifier,
               mine: Boolean = true, selected: Boolean = false) {
    val app = LocalContext.current.applicationContext as? MailrApp
    val flow: StateFlow<Me?> = remember(app) {
        catching { app?.graph?.mail?.me }.getOrNull() ?: MutableStateFlow(null)
    }
    val me by flow.collectAsState()
    val path = if (selected || app == null) null else OwnPicture.path(me, email, mine)
    val picture by produceState<ImageBitmap?>(null, path) {
        value = path?.let { p -> withContext(Dispatchers.IO) { PictureCache.get(app!!, p) } }
    }
    Box(modifier.size(size)) {
        Avatar(name, email, size = size, selected = selected)
        picture?.let {
            Image(it, contentDescription = null, contentScale = ContentScale.Crop,
                modifier = Modifier.matchParentSize().clip(CircleShape))
        }
    }
}
