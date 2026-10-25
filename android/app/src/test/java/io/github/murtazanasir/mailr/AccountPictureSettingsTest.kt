// ACT-SURFACES: android.settings
package io.github.murtazanasir.mailr

import android.app.Application
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.media.ExifInterface
import androidx.compose.ui.test.assertIsNotEnabled
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.AccountPicture
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.ui.settings.AccountPictureImage
import io.github.murtazanasir.mailr.ui.settings.AccountPictures
import io.github.murtazanasir.mailr.ui.settings.PictureCallbacks
import io.github.murtazanasir.mailr.ui.settings.PictureSection
import io.github.murtazanasir.mailr.ui.settings.PictureUi
import io.github.murtazanasir.mailr.ui.settings.accountPicturePath
import io.github.murtazanasir.mailr.ui.settings.offersSignInPicture
import io.github.murtazanasir.mailr.ui.settings.pictureSourceText
import kotlinx.coroutines.runBlocking
import okhttp3.MultipartReader
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Settings → Your picture on the phone (android.settings): settings.picture_upload (reduced to
 * 1024 px and re-encoded as JPEG without EXIF before PUT /me/picture), settings.picture_remove
 * (DELETE /me/picture) and settings.picture_use_sign_in (POST /me/picture/use-sign-in), against a
 * fake server with the server's contract. Each change reads GET /me again.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class AccountPictureSettingsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private lateinit var server: MockWebServer
    private lateinit var g: AppGraph
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    private val bodies = ConcurrentHashMap<RecordedRequest, ByteArray>()
    @Volatile private var signInStatus = 200
    @Volatile private var picture = """{"has":false,"source":null,"version":0,"idp_available":true}"""

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                bodies[request] = request.body.readByteArray()
                requests += request
                val path = request.path.orEmpty()
                return when {
                    path == "/api/v1/me" -> MockResponse().setBody(
                        """{"user":{"id":1,"email":"me@example.com","name":"Me"},"identities":[],"picture":$picture}""")
                    path == "/api/v1/me/picture" && request.method == "PUT" -> {
                        picture = """{"has":true,"source":"upload","version":1700000001,"idp_available":true}"""
                        MockResponse().setBody(picture)
                    }
                    path == "/api/v1/me/picture" && request.method == "DELETE" -> {
                        picture = """{"has":false,"source":null,"version":0,"idp_available":true}"""
                        MockResponse().setBody(picture)
                    }
                    path == "/api/v1/me/picture/use-sign-in" && request.method == "POST" ->
                        if (signInStatus != 200) MockResponse().setResponseCode(signInStatus)
                            .setBody("""{"detail":"your sign-in provider's picture is on a host this server does not fetch from"}""")
                        else {
                            picture = """{"has":true,"source":"idp","version":1700000002,"idp_available":true}"""
                            MockResponse().setBody(picture)
                        }
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        server.start()
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        SessionStore(ctx).apply {
            server = this@AccountPictureSettingsTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
        g = graphs.graph(ctx)
    }

    @After fun tearDown() {
        graphs.close()
        server.shutdown()
    }

    private fun bitmap(w: Int, h: Int): Bitmap = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888).also {
        Canvas(it).apply {
            drawColor(Color.rgb(30, 90, 160))
            drawRect(0f, 0f, w / 4f, h / 4f, Paint().apply { color = Color.RED })
        }
    }

    private fun jpeg(w: Int, h: Int): ByteArray =
        ByteArrayOutputStream().also { bitmap(w, h).compress(Bitmap.CompressFormat.JPEG, 95, it) }.toByteArray()

    /** A camera-like JPEG: GPS position, camera make and an orientation tag. */
    private fun withExif(bytes: ByteArray, orientation: Int = ExifInterface.ORIENTATION_NORMAL): ByteArray {
        val f = File.createTempFile("exif", ".jpg").apply { writeBytes(bytes) }
        ExifInterface(f.absolutePath).apply {
            setAttribute(ExifInterface.TAG_GPS_LATITUDE, "51/1,30/1,0/1")
            setAttribute(ExifInterface.TAG_GPS_LATITUDE_REF, "N")
            setAttribute(ExifInterface.TAG_GPS_LONGITUDE, "0/1,7/1,0/1")
            setAttribute(ExifInterface.TAG_GPS_LONGITUDE_REF, "W")
            setAttribute(ExifInterface.TAG_MAKE, "PhoneMaker")
            setAttribute(ExifInterface.TAG_ORIENTATION, orientation.toString())
            saveAttributes()
        }
        return f.readBytes().also { f.delete() }
    }

    private fun size(bytes: ByteArray): Pair<Int, Int> {
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, o)
        return o.outWidth to o.outHeight
    }

    private fun hasExifSegment(bytes: ByteArray): Boolean = String(bytes, Charsets.ISO_8859_1).contains("Exif\u0000\u0000")

    // ---------- the phone's copy (settings.picture_upload) ----------

    @Test fun reducesToTheLongestSideAndNeverEnlarges() {
        assertEquals(1024 to 683, AccountPictureImage.targetSize(3000, 2000))
        assertEquals(683 to 1024, AccountPictureImage.targetSize(2000, 3000))
        assertEquals(500 to 300, AccountPictureImage.targetSize(500, 300))
        assertEquals(1024 to 1024, AccountPictureImage.targetSize(1024, 1024))
        assertEquals(1024 to 683, size(AccountPictureImage.prepare(jpeg(3000, 2000))))
        assertEquals(500 to 300, size(AccountPictureImage.prepare(jpeg(500, 300))))
    }

    @Test fun reEncodingDropsTheLocationAndCameraData() {
        val original = withExif(jpeg(1600, 1200))
        assertTrue("the fixture carries EXIF", hasExifSegment(original))
        assertNotNull(ExifInterface(original.inputStream()).getAttribute(ExifInterface.TAG_GPS_LATITUDE))
        val out = AccountPictureImage.prepare(original)
        assertFalse("no EXIF segment after the re-encode", hasExifSegment(out))
        val exif = ExifInterface(out.inputStream())
        assertNull(exif.getAttribute(ExifInterface.TAG_GPS_LATITUDE))
        assertNull(exif.getAttribute(ExifInterface.TAG_MAKE))
        assertEquals(0xff, out[0].toInt() and 0xff)
        assertEquals(0xd8, out[1].toInt() and 0xff)
    }

    @Test fun theOrientationIsAppliedBeforeTheTagGoes() {
        val out = AccountPictureImage.prepare(withExif(jpeg(1600, 1200), ExifInterface.ORIENTATION_ROTATE_90))
        assertEquals(768 to 1024, size(out))
    }

    @Test fun aFileThatIsNoPictureIsRefused() {
        try {
            AccountPictureImage.prepare("not a picture".toByteArray())
            fail("expected Refused")
        } catch (e: AccountPictureImage.Refused) {
            assertTrue(e.message!!.contains("Couldn't read"))
        }
    }

    // ---------- the server calls ----------

    @Test fun uploadPutsTheReducedJpegAndReadsMeAgain() = runBlocking {
        val got = AccountPictures(g.api, g.mail).upload(withExif(jpeg(3000, 2000)))
        assertTrue(got.has)
        assertEquals("upload", got.source)
        val put = requests.single { it.method == "PUT" && it.path == "/api/v1/me/picture" }
        assertEquals("csrf-456", put.getHeader("X-CSRF-Token"))
        assertTrue(put.getHeader("Cookie")!!.contains("mailr_session=tok-123"))
        val boundary = put.getHeader("Content-Type")!!.substringAfter("boundary=")
        MultipartReader(Buffer().write(bodies[put]!!), boundary).use { r ->
            val part = r.nextPart()!!
            assertTrue(part.headers["Content-Disposition"]!!.contains("name=\"file\""))
            assertEquals("image/jpeg", part.headers["Content-Type"])
            val bytes = part.body.readByteArray()
            assertEquals(1024 to 683, size(bytes))
            assertFalse(hasExifSegment(bytes))
        }
        assertTrue("GET /me after the change", requests.any { it.method == "GET" && it.path == "/api/v1/me" })
        assertEquals(1700000001L, g.mail.me.value?.picture?.version)
    }

    @Test fun removeDeletesThePicture() = runBlocking {
        val got = AccountPictures(g.api, g.mail).remove()
        assertFalse(got.has)
        assertNotNull(requests.singleOrNull { it.method == "DELETE" && it.path == "/api/v1/me/picture" })
        assertEquals(false, g.mail.me.value?.picture?.has)
    }

    @Test fun useSignInAsksTheServerAndShowsItsReason() = runBlocking {
        val got = AccountPictures(g.api, g.mail).useSignIn()
        assertEquals("idp", got.source)
        assertNotNull(requests.singleOrNull { it.method == "POST" && it.path == "/api/v1/me/picture/use-sign-in" })
        signInStatus = 409
        try {
            AccountPictures(g.api, g.mail).useSignIn()
            fail("expected 409")
        } catch (e: ApiException) {
            assertEquals(409, e.code)
            assertTrue(e.detail.contains("does not fetch from"))
        }
    }

    // ---------- what the section offers ----------

    @Test fun theSectionsTextAndButtonsFollowThePicture() {
        assertEquals("No picture. Your initials show.", pictureSourceText(null))
        assertEquals("Uploaded by you.", pictureSourceText(AccountPicture(true, "upload", 1)))
        assertEquals("From your sign-in provider.", pictureSourceText(AccountPicture(true, "idp", 1)))
        assertTrue(offersSignInPicture(AccountPicture(true, "upload", 1, idpAvailable = true)))
        assertFalse(offersSignInPicture(AccountPicture(true, "idp", 1, idpAvailable = true)))
        assertFalse(offersSignInPicture(AccountPicture(false, idpAvailable = false)))
        assertEquals("/api/v1/me/picture?v=5", accountPicturePath(AccountPicture(true, "upload", 5)))
        assertNull(accountPicturePath(AccountPicture(false)))
    }

    @Test fun changeOffersTheGalleryAndTheCameraAndRemoveCalls() {
        val calls = mutableListOf<String>()
        compose.setContent {
            PictureSection("Me", "me@example.com", PictureUi(AccountPicture(true, "upload", 1, idpAvailable = true)),
                PictureCallbacks(pick = { calls += "pick" }, take = { calls += "take" },
                    remove = { calls += "remove" }, useSignIn = { calls += "sign-in" }))
        }
        compose.onNodeWithTag("picture-source").assertTextEquals("Uploaded by you.")
        compose.onNodeWithText("Change").performClick()
        compose.onNodeWithText("Choose from gallery").performClick()
        compose.onNodeWithText("Change").performClick()
        compose.onNodeWithText("Take photo").performClick()
        compose.onNodeWithText("Remove").performClick()
        compose.onNodeWithText("Use my sign-in picture").performClick()
        assertEquals(listOf("pick", "take", "remove", "sign-in"), calls)
    }

    @Test fun withoutAPictureThereIsNothingToRemoveAndBusyDisablesChanges() {
        compose.setContent {
            PictureSection("Me", "me@example.com", PictureUi(AccountPicture(false, idpAvailable = false), busy = true), PictureCallbacks())
        }
        compose.onNodeWithText("Remove").assertDoesNotExist()
        compose.onNodeWithText("Use my sign-in picture").assertDoesNotExist()
        compose.onNodeWithText("Add picture").assertIsNotEnabled()
    }
}
