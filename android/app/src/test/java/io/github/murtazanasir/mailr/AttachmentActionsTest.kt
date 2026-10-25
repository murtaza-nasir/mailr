// ACT-SURFACES: android.reader
package io.github.murtazanasir.mailr

import android.app.Application
import android.content.Intent
import android.net.Uri
import android.provider.MediaStore
import androidx.compose.material3.SnackbarHostState
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.Attachment
import io.github.murtazanasir.mailr.data.api.Message
import io.github.murtazanasir.mailr.data.files.Downloads
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.conversation.BodyState
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.conversation.attachmentShareIntent
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.io.File

/**
 * ACT mail.attachment_share and ACT mail.attachment_save on android.reader: a long press on an
 * attachment offers Open, Share and Save to Downloads (TalkBack lists Share and Save as actions);
 * Share hands the downloaded file to the share sheet through the FileProvider; Save copies it into
 * Downloads through MediaStore.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class AttachmentActionsTest {
    @get:Rule val compose = createComposeRule()
    private val ctx get() = ApplicationProvider.getApplicationContext<Application>()
    private val pdf = Attachment(2, "PADS_2027_CFP.pdf", "application/pdf", 482_000)

    private fun downloaded(): File = File(ctx.cacheDir, "attachment-files/502").apply { mkdirs() }
        .resolve("PADS_2027_CFP.pdf").apply { writeBytes(byteArrayOf(37, 80, 68, 70)) }

    @Test fun aLongPressOffersShareAndSave() {
        val shared = mutableListOf<Long>()
        val saved = mutableListOf<Long>()
        val opened = mutableListOf<Long>()
        val actions = ConversationActions(back = {}, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
            openAttachment = { m, _ -> opened += m.id }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {},
            mailto = {}, retry = {}, shareAttachment = { m: Message, _: Attachment -> shared += m.id },
            saveAttachment = { m: Message, _: Attachment -> saved += m.id })
        compose.setContent {
            MailrTheme(ThemeMode.LIGHT, dynamic = false) {
                ConversationScreen(Fixtures.detail, null, Fixtures.labels, setOf(502L),
                    mapOf(502L to BodyState.Ready(io.github.murtazanasir.mailr.data.api.MessageBody(html = "<p>x</p>", source = "html"), false)),
                    null, null, false, SnackbarHostState(), actions)
            }
        }
        compose.onNodeWithTag("attachment-2").performTouchInput { longClick() }
        compose.waitForIdle()
        // The long press did not open the file.
        assertTrue(opened.isEmpty())
        compose.onNodeWithText("Save to Downloads").performClick()
        compose.waitForIdle()
        assertEquals(listOf(502L), saved)
        // TalkBack: Share is one of the chip's actions.
        val custom = compose.onNodeWithTag("attachment-2").fetchSemanticsNode().config[SemanticsActions.CustomActions]
        assertEquals(listOf("Share", "Save to Downloads"), custom.map { it.label })
        compose.runOnIdle { custom.first().action() }
        assertEquals(listOf(502L), shared)
        // A tap still opens it.
        compose.onNodeWithText("PADS_2027_CFP.pdf", substring = true).performClick()
        compose.waitForIdle()
        assertEquals(listOf(502L), opened)
    }

    @Test fun shareSendsTheDownloadedFileThroughTheFileProvider() {
        val chooser = attachmentShareIntent(ctx, downloaded(), pdf)
        assertEquals(Intent.ACTION_CHOOSER, chooser.action)
        @Suppress("DEPRECATION") val send = chooser.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)!!
        assertEquals(Intent.ACTION_SEND, send.action)
        assertEquals("application/pdf", send.type)
        @Suppress("DEPRECATION") val uri = send.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)!!
        assertEquals("content", uri.scheme)
        assertEquals(ctx.packageName + ".files", uri.authority)
        assertTrue(send.flags and Intent.FLAG_GRANT_READ_URI_PERMISSION != 0)
    }

    @Test fun saveCopiesIntoDownloads() = runBlocking {
        val uri = Downloads.save(ctx, downloaded(), "PADS_2027_CFP.pdf", "application/pdf")
        val insert = shadowOf(ctx.contentResolver).insertStatements.single()
        assertEquals(MediaStore.Downloads.EXTERNAL_CONTENT_URI, insert.uri)
        assertEquals("PADS_2027_CFP.pdf", insert.contentValues.getAsString(MediaStore.MediaColumns.DISPLAY_NAME))
        assertEquals("application/pdf", insert.contentValues.getAsString(MediaStore.MediaColumns.MIME_TYPE))
        assertEquals("Download", insert.contentValues.getAsString(MediaStore.MediaColumns.RELATIVE_PATH))
        // Pending while the bytes are copied, then shown.
        assertEquals(1, insert.contentValues.getAsInteger(MediaStore.MediaColumns.IS_PENDING))
        val update = shadowOf(ctx.contentResolver).updateStatements.single()
        assertEquals(uri, update.uri)
        assertEquals(0, update.contentValues.getAsInteger(MediaStore.MediaColumns.IS_PENDING))
    }
}
