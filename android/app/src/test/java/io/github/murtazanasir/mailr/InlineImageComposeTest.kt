// ACT-SURFACES: android.editor
package io.github.murtazanasir.mailr

import android.app.Application
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.compose.BodyBlock
import io.github.murtazanasir.mailr.ui.compose.ComposeArgs
import io.github.murtazanasir.mailr.ui.compose.ComposeBody
import io.github.murtazanasir.mailr.ui.compose.ComposeViewModel
import io.github.murtazanasir.mailr.ui.compose.Field
import io.github.murtazanasir.mailr.ui.compose.InlinePhotos
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MultipartReader
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.ByteArrayOutputStream
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Android compose keeps a draft's HTML and puts photos into the body (ACT mail.insert_image,
 * android.editor), against a fake server with the server's contract: a photo goes up through
 * POST /uploads ("file" part), and the body points at it as <img src="/api/v1/uploads/<blob>">,
 * which core/compose.py turns into a cid: part.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class InlineImageComposeTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    private lateinit var server: MockWebServer
    private lateinit var g: AppGraph
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    private val bodies = ConcurrentHashMap<RecordedRequest, ByteArray>()
    private val blob = "0123456789abcdef0123456789abcdef"
    private val draftBlob = "fedcba9876543210fedcba9876543210"

    private val me = """{"user":{"id":1,"email":"me@example.com","name":"Me"},
        "identities":[{"id":7,"email":"me@example.com","name":"Me","is_default":true,"signature_html":"<b>Me</b>"}]}"""

    /** A draft written on the web: formatting, a list, a centred image and a link. */
    private val webBody = "<p>Hi <strong>Alice</strong>,</p>" +
        "<p>The plan:</p><ul><li><p>Budget</p></li><li><p>Hiring</p></li></ul>" +
        "<p><img src=\"/api/v1/uploads/$draftBlob\" alt=\"chart\" width=\"400\" height=\"300\" data-align=\"center\" " +
        "style=\"max-width:100%;height:auto;display:block;margin-left:auto;margin-right:auto\"></p>" +
        "<p>See <a href=\"https://example.com/plan\">the plan</a>.</p><p>Thanks</p>"

    private val quote = "<div class=\"mailr_quote\"><p>On Mon, Bob wrote:</p><blockquote><table><tr><td><b>Q3</b></td></tr></table></blockquote></div>"

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                bodies[request] = request.body.readByteArray()
                requests += request
                val path = request.path.orEmpty()
                val m = request.method
                return when {
                    path == "/api/v1/me" -> MockResponse().setBody(me)
                    path == "/api/v1/uploads" && m == "POST" ->
                        MockResponse().setBody("""{"blob":"$blob","filename":"photo.jpg","type":"image/jpeg","size":1}""")
                    path == "/api/v1/uploads/$draftBlob" -> MockResponse().setHeader("Content-Type", "image/png")
                        .setBody(Buffer().write(png(40, 30)))
                    path == "/api/v1/drafts/88" && m == "GET" -> MockResponse().setBody(
                        """{"draft":{"id":88,"draft":true},"payload":{"to":[{"email":"alice@example.com"}],"subject":"Plan","html":${JsonPrimitive(webBody)}}}""")
                    path == "/api/v1/drafts/88" && m == "PUT" -> MockResponse().setBody("""{"draft":{"id":88,"draft":true}}""")
                    path == "/api/v1/drafts" && m == "POST" -> MockResponse().setBody("""{"draft":{"id":900,"draft":true}}""")
                    path.startsWith("/api/v1/compose/reply") -> MockResponse().setBody(
                        """{"payload":{"to":[{"email":"bob@example.com"}],"subject":"Re: Q3","in_reply_to_message_id":502,"html":${JsonPrimitive("<div><br></div>$quote")}}}""")
                    path == "/api/v1/send" -> MockResponse().setBody("""{"outbox":{"id":55,"status":"pending"}}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        server.start()
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        SessionStore(ctx).apply {
            server = this@InlineImageComposeTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
        g = graphs.graph(ctx)
    }

    @After fun tearDown() {
        idle()
        graphs.close()
        server.shutdown()
    }

    // ---------- helpers ----------

    private fun idle() = shadowOf(Looper.getMainLooper()).idle()

    private fun request(method: String, path: String): RecordedRequest {
        eventually("$method $path") { requests.any { it.method == method && it.path == path } }
        return requests.last { it.method == method && it.path == path }
    }

    private fun payloadOf(r: RecordedRequest) =
        MailrJson.parseToJsonElement(bodies[r]!!.decodeToString()).jsonObject["payload"]!!.jsonObject

    private fun composer(args: ComposeArgs): ComposeViewModel {
        val vm = graphs.vm { ComposeViewModel(g, args) }
        // The sending addresses (and so the signature) come in on their own (GET /me).
        eventually("composer loaded") { !vm.state.value.loading && vm.state.value.identities.isNotEmpty() }
        return vm
    }

    private fun bitmap(w: Int, h: Int): Bitmap = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888).also { b ->
        val c = Canvas(b)
        c.drawColor(Color.rgb(40, 90, 160))
        c.drawCircle(w / 2f, h / 2f, minOf(w, h) / 3f, Paint().apply { color = Color.rgb(240, 180, 40) })
    }

    private fun jpeg(w: Int, h: Int): ByteArray = ByteArrayOutputStream().also { bitmap(w, h).compress(Bitmap.CompressFormat.JPEG, 95, it) }.toByteArray()
    private fun png(w: Int, h: Int): ByteArray = ByteArrayOutputStream().also { bitmap(w, h).compress(Bitmap.CompressFormat.PNG, 100, it) }.toByteArray()

    /** The "file" part of a multipart upload: (headers, bytes). */
    private fun filePart(r: RecordedRequest): Pair<String, ByteArray> {
        val boundary = r.getHeader("Content-Type")!!.substringAfter("boundary=")
        MultipartReader(Buffer().write(bodies[r]!!), boundary).use { reader ->
            val part = reader.nextPart()!!
            return part.headers.toString() to part.body.readByteArray()
        }
    }

    // ---------- HTML round trip ----------

    /** A web draft opened and saved on the phone goes back byte for byte. */
    @Test fun webDraftRoundTripIsExact() {
        val parsed = ComposeBody.parse(webBody + quote)
        assertEquals(quote, parsed.quote)
        assertEquals(webBody, ComposeBody.toHtml(parsed.blocks))
        // The formatted parts are kept as HTML; the plain paragraphs are text to edit.
        assertTrue(parsed.blocks.any { it is BodyBlock.Html && "<strong>Alice</strong>" in it.html })
        assertTrue(parsed.blocks.any { it is BodyBlock.Html && it.html.startsWith("<ul>") })
        assertTrue(parsed.blocks.any { it is BodyBlock.Image && it.blob == draftBlob && it.width == 400 })
        assertTrue(parsed.blocks.any { it is BodyBlock.Text && it.text == "Thanks" })
    }

    /** Editing the plain text of a web draft keeps every formatted part as written. */
    @Test fun draftEditKeepsFormatting() {
        val vm = composer(ComposeArgs(draftId = 88))
        request("GET", "/api/v1/uploads/$draftBlob")
        eventually("draft image shown") { vm.state.value.previews.isNotEmpty() }
        val thanks = vm.state.value.blocks.first { it is BodyBlock.Text && it.text == "Thanks" }
        vm.setText(thanks.key, "Thanks,\nRowan")
        vm.close()
        val html = payloadOf(request("PUT", "/api/v1/drafts/88"))["html"]!!.jsonPrimitive.content
        assertTrue(html, html.startsWith("<p>Hi <strong>Alice</strong>,</p><p>The plan:</p><ul><li><p>Budget</p></li><li><p>Hiring</p></li></ul>"))
        assertTrue(html, "<img src=\"/api/v1/uploads/$draftBlob\" alt=\"chart\" width=\"400\" height=\"300\" data-align=\"center\"" in html)
        assertTrue(html, "<p>See <a href=\"https://example.com/plan\">the plan</a>.</p>" in html)
        assertTrue(html, "<p>Thanks,<br>Rowan</p>" in html)
        assertFalse(html, "Thanks</p>" in html)
    }

    /** A reply keeps the quoted HTML (tables, bold) as it came from the server. */
    @Test fun replyKeepsQuotedHtml() {
        val vm = composer(ComposeArgs(replyToMessageId = 502, mode = "reply"))
        vm.setBody("Looks good.")
        vm.send()
        val html = payloadOf(request("POST", "/api/v1/send"))["html"]!!.jsonPrimitive.content
        assertTrue(html, html.startsWith("<p>Looks good.</p>"))
        assertTrue(html, html.endsWith(quote))
    }

    /** A forward keeps the forwarded message as written, after what the user types. */
    @Test fun forwardKeepsMessage() {
        val fwd = "<div><br></div><br><div>---------- Forwarded message ---------<br>From: Bob<br></div><br>" +
            "<table><tr><td style=\"color:red\">Q3</td></tr></table>"
        val parsed = ComposeBody.parse(fwd)
        assertEquals("<br><div>---------- Forwarded message ---------<br>From: Bob<br></div><br><table><tr><td style=\"color:red\">Q3</td></tr></table>",
            parsed.quote)
        val edited = ComposeBody.setText(parsed.blocks, parsed.blocks.first().key, "FYI")
        assertEquals("<p>FYI</p>", ComposeBody.toHtml(edited))
    }

    /** A saved signature is kept once, in its place; it is not added a second time. */
    @Test fun signatureNotDoubled() {
        val vm = composer(ComposeArgs())
        vm.addRecipient(Field.TO, "alice@example.com")
        vm.setBody("Hello")
        val first = vm.payload().html!!
        assertEquals(1, Regex("mailr_signature").findAll(first).count())
        val again = ComposeBody.parse(first)
        assertEquals(first, ComposeBody.toHtml(again.blocks) + again.signature)
    }

    /** HTML that cannot be split safely is kept whole. */
    @Test fun malformedHtmlKeptWhole() {
        val bad = "<p>one<p>two <b>bold"
        val parsed = ComposeBody.parse(bad)
        assertEquals(bad, ComposeBody.toHtml(parsed.blocks))
        assertTrue(parsed.blocks.any { it is BodyBlock.Html })
    }

    // ---------- inline photos ----------

    /** A large photo is reduced like the web (2560 px, JPEG), uploaded as the "file" part of
     *  POST /uploads, and goes in at the cursor; the send body points at the upload. */
    @Test fun photoInsertedAtCursorAndSent() {
        val vm = composer(ComposeArgs())
        vm.addRecipient(Field.TO, "alice@example.com")
        vm.setBody("Before the photo.\nAfter the photo.")
        val text = vm.state.value.blocks.single()
        vm.setCursor(text.key, "Before the photo.\n".length)
        vm.insertPhoto(jpeg(3000, 2000), "IMG_1234.jpg", "image/jpeg")
        eventually("image placed") { vm.state.value.blocks.any { it is BodyBlock.Image } }
        val (headers, bytes) = filePart(request("POST", "/api/v1/uploads"))
        assertTrue(headers, "name=\"file\"" in headers && "filename=\"IMG_1234.jpg\"" in headers)
        assertTrue(headers, "image/jpeg" in headers)
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, o)
        assertEquals(2560 to 1707, o.outWidth to o.outHeight)
        eventually("upload done") { vm.state.value.imagesUploading == 0 }
        val blocks = vm.state.value.blocks
        assertEquals(listOf("Before the photo.", "image", "After the photo."),
            blocks.map { if (it is BodyBlock.Text) it.text else "image" })
        vm.send()
        val html = payloadOf(request("POST", "/api/v1/send"))["html"]!!.jsonPrimitive.content
        assertTrue(html, html.startsWith("<p>Before the photo.</p><p><img src=\"/api/v1/uploads/$blob\" alt=\"\" width=\"600\" height=\"400\" " +
            "style=\"max-width:100%;height:auto\"></p><p>After the photo.</p>"))
    }

    /** A small PNG goes up as it is; it shows at its own width. */
    @Test fun smallPhotoUnchanged() {
        val vm = composer(ComposeArgs())
        val src = png(320, 200)
        vm.insertPhoto(src, "shot.png", "image/png")
        val (_, bytes) = filePart(request("POST", "/api/v1/uploads"))
        assertArrayEquals(src, bytes)
        eventually("upload done") { vm.state.value.blocks.any { it is BodyBlock.Image && !it.uploading } }
        val img = vm.state.value.blocks.filterIsInstance<BodyBlock.Image>().single()
        assertEquals(320 to 200, img.width to img.height)
        assertNotNull(vm.state.value.previews[img.key])
    }

    /** While a photo uploads it is left out of the body, and Send waits for it. */
    @Test fun uploadingPhotoHeldBack() {
        val blocks = ComposeBody.insert(ComposeBody.ofText("Hi"), null, 0, BodyBlock.Image(ComposeBody.nextKey(), null, 10, 10))
        assertEquals("<p>Hi</p>", ComposeBody.toHtml(blocks))
    }

    /** Remove takes the image out and joins the text around it. */
    @Test fun removeJoinsText() {
        val img = BodyBlock.Image(ComposeBody.nextKey(), blob, 600, 400)
        val start = ComposeBody.ofText("one\ntwo")
        val with = ComposeBody.insert(start, start.single().key, 4, img)
        assertEquals(3, with.size)
        val back = ComposeBody.remove(with, img.key)
        assertEquals(listOf("one\ntwo"), back.map { (it as BodyBlock.Text).text })
    }

    /** The web's limits: SVG is refused; a GIF is never redrawn. */
    @Test fun webRules() {
        try {
            InlinePhotos.prepare("<svg/>".toByteArray(), "logo.svg", "image/svg+xml")
            fail("SVG accepted")
        } catch (e: InlinePhotos.Refused) {
            assertTrue(e.message!!.contains("SVGs can only be attached"))
        }
        assertFalse(InlinePhotos.shouldDownscale(4000, 4000, 9_000_000, "image/gif"))
        assertTrue(InlinePhotos.shouldDownscale(1000, 800, 3 * 1024 * 1024 + 1, "image/jpeg"))
        assertEquals(2560 to 1440, InlinePhotos.downscaleSize(4000, 2250))
        assertEquals(600 to 450, InlinePhotos.initialSize(1200, 900))
    }
}
