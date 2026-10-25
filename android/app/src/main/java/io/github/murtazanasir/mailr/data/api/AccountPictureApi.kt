package io.github.murtazanasir.mailr.data.api

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

// The account picture (backend api/account_picture_routes.py). Changing
// it needs the signed-in session, which the phone has; an API token cannot. Each call answers with
// the picture info that GET /me carries as `picture`.

/** Upload a picture the phone already reduced and re-encoded (AccountPictureImage.prepare). Same
 *  multipart form as the web ("file"); the server re-encodes it again. */
// ACT settings.picture_upload @android.settings
suspend fun MailrApi.uploadPicture(jpeg: ByteArray): AccountPicture {
    val body = MultipartBody.Builder().setType(MultipartBody.FORM)
        .addFormDataPart("file", "picture.jpg", jpeg.toRequestBody("image/jpeg".toMediaType())).build()
    return client.execute(Request.Builder().url(client.url("me/picture")).put(body).build(), AccountPicture.serializer())
}

// ACT settings.picture_remove @android.settings
suspend fun MailrApi.removePicture(): AccountPicture =
    client.send("DELETE", "me/picture", AccountPicture.serializer())

/** The sign-in provider's picture again (409 or 404 when the server cannot fetch it; the detail says why). */
// ACT settings.picture_use_sign_in @android.settings
suspend fun MailrApi.useSignInPicture(): AccountPicture =
    client.send("POST", "me/picture/use-sign-in", AccountPicture.serializer())
