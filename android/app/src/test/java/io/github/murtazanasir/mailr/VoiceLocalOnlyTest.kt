// ACT-SURFACES: android.ask
package io.github.murtazanasir.mailr

import android.app.Application
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import io.github.murtazanasir.mailr.data.voice.DictationOrder
import io.github.murtazanasir.mailr.data.voice.VoicePolicy
import io.github.murtazanasir.mailr.data.voice.VoiceSource
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Dictation under local models only (agent.dictate): no source
 * can reach a cloud recognizer. The phone's default SpeechRecognizer (which may send audio to its
 * maker) is never created anywhere in the app, the on-device request prefers offline recognition,
 * and the policy only ever picks the phone's on-device recognizer or the user's own speech server.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class VoiceLocalOnlyTest {
    private val sources = File("src/main/java")

    @Test fun theDefaultRecognizerIsNeverCreated() {
        assertTrue("run from the app module", sources.isDirectory)
        val hits = sources.walkTopDown().filter { it.isFile && it.extension == "kt" }
            .filter { f -> Regex("""SpeechRecognizer\.createSpeechRecognizer\(""").containsMatchIn(f.readText()) }
            .map { it.path }.toList()
        assertEquals("the default recognizer may be a cloud one", emptyList<String>(), hits)
    }

    @Test fun theOnDeviceRequestPrefersOffline() {
        val text = File(sources, "io/github/murtazanasir/mailr/data/voice/Dictation.kt").readText()
        assertTrue(text.contains("createOnDeviceSpeechRecognizer"))
        assertTrue(text.contains("EXTRA_PREFER_OFFLINE, true"))
    }

    @Test fun thePolicyPicksOnlyLocalSources() {
        for (onDevice in listOf(true, false)) for (server in listOf(true, false)) for (order in DictationOrder.entries) {
            val s = VoicePolicy.source(onDevice, server, order)
            when (s) {
                VoiceSource.ON_DEVICE -> assertTrue(onDevice)
                VoiceSource.SERVER -> assertTrue(server)
                VoiceSource.NONE -> assertTrue(!onDevice && !server)
            }
            assertTrue(VoicePolicy.describe(onDevice, server, order).endsWith("go to a local speech server only."))
        }
        // The phone first (the default): on-device when the phone has it.
        assertEquals(VoiceSource.ON_DEVICE, VoicePolicy.source(true, true, DictationOrder.PHONE_FIRST))
    }
}
