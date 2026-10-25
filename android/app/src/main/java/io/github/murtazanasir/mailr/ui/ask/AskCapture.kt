package io.github.murtazanasir.mailr.ui.ask

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.data.voice.Dictation
import io.github.murtazanasir.mailr.data.voice.VoiceSource
import io.github.murtazanasir.mailr.data.voice.VoiceState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** What the ask capture shows. [text] is what Send sends; while listening it already holds the words heard so far. */
data class AskUi(
    val about: AskAbout?,
    val text: String = "",
    val voice: VoiceState = VoiceState.Idle,
    val source: VoiceSource = VoiceSource.NONE,
    /** "Send when I stop talking": the words go as soon as the recognizer finishes. */
    val handsFree: Boolean = false,
    val sent: Boolean = false,
) {
    val listening get() = voice is VoiceState.Listening
    val transcribing get() = voice == VoiceState.Transcribing
    val canSend get() = text.isNotBlank() && !sent && !transcribing
}

/**
 * The ask capture's state (ACT agent.dictate @android.ask, ACT agent.ask_about @android.ask). Typed words and dictated
 * words share one editable field: dictation adds to what is already there. Nothing is sent until
 * Send, or until the recognizer finishes in hands-free mode. [submit] hands the request to the
 * app's Assistant tab.
 */
class AskCaptureModel(
    about: AskAbout?,
    source: VoiceSource,
    handsFree: Boolean,
    /** A recognizer for a source (null when that source cannot be made). */
    private val engines: (VoiceSource) -> Dictation?,
    private val scope: CoroutineScope,
    /** The other source to try when the current one cannot be used (VoicePolicy.fallback). */
    private val fallback: (VoiceSource) -> VoiceSource? = { null },
    private val submit: (AskRequest) -> Unit,
) {
    private var dictation: Dictation? = engines(source)
    private val _ui = MutableStateFlow(AskUi(about, source = if (dictation == null) VoiceSource.NONE else source, handsFree = handsFree))
    val ui: StateFlow<AskUi> = _ui.asStateFlow()
    /** The text before the current dictation started; dictated words are added after it. */
    private var base = ""
    private var watch: Job? = null

    fun setText(t: String) = _ui.update { it.copy(text = t) }

    fun setHandsFree(v: Boolean) = _ui.update { it.copy(handsFree = v) }

    /** Drops the item: the instruction goes to the assistant without it. */
    fun clearAbout() = _ui.update { it.copy(about = null) }

    /** The server's answer to "can you transcribe?" came in: use it if nothing on the phone can. */
    fun setSource(s: VoiceSource) {
        if (_ui.value.source == s || _ui.value.listening) return
        dictation?.destroy()
        dictation = engines(s)
        _ui.update { it.copy(source = if (dictation == null) VoiceSource.NONE else s, voice = VoiceState.Idle) }
    }

    fun listen() {
        val d = dictation ?: return
        if (_ui.value.listening || _ui.value.voice == VoiceState.Transcribing || _ui.value.sent) return
        base = _ui.value.text.trimEnd()
        watch?.cancel()
        watch = scope.launch {
            d.state.collect { v ->
                when (v) {
                    is VoiceState.Listening -> _ui.update { it.copy(voice = v, text = join(base, v.partial)) }
                    is VoiceState.Heard -> {
                        _ui.update { it.copy(voice = v, text = join(base, v.text)) }
                        if (_ui.value.handsFree && v.text.isNotBlank()) send()
                    }
                    is VoiceState.Failed -> {
                        val other = if (v.unusable) fallback(_ui.value.source) else null
                        if (other != null && switchTo(other)) return@collect
                        _ui.update { it.copy(voice = v, text = if (it.text.isBlank()) base else it.text) }
                    }
                    VoiceState.Idle, VoiceState.Transcribing -> _ui.update { it.copy(voice = v) }
                }
            }
        }
        d.start()
    }

    /** This recognizer cannot serve the request: listen again with [other], keeping the typed words. */
    private fun switchTo(other: VoiceSource): Boolean {
        val next = engines(other) ?: return false
        watch?.cancel()
        dictation?.destroy()
        dictation = next
        _ui.update { it.copy(source = other, voice = VoiceState.Idle, text = base) }
        scope.launch { listen() }
        return true
    }

    /** Stops listening and keeps what was heard (the recognizer delivers its final words). */
    fun stopListening() {
        dictation?.stop()
    }

    fun cancelListening() {
        dictation?.cancel()
        _ui.update { it.copy(voice = VoiceState.Idle) }
    }

    fun download(): Boolean = dictation?.download() ?: false

    /**
     * The Assistant tab's message box after it sent: an empty field. A clip still being transcribed
     * keeps its state, so its words land in the empty field.
     */
    fun clear() {
        base = ""
        _ui.update { it.copy(text = "", voice = if (it.listening || it.transcribing) it.voice else VoiceState.Idle) }
    }

    fun send() {
        val s = _ui.value
        if (!s.canSend) return
        if (s.listening) dictation?.cancel()
        _ui.update { it.copy(sent = true, voice = VoiceState.Idle) }
        submit(AskRequest(s.about, s.text.trim()))
    }

    /** "Open chat": the Assistant tab about the item, with nothing sent. */
    fun openChat() {
        val s = _ui.value
        if (s.sent) return
        if (s.listening) dictation?.cancel()
        _ui.update { it.copy(sent = true, voice = VoiceState.Idle) }
        submit(AskRequest(s.about, null))
    }

    fun close() {
        watch?.cancel()
        dictation?.destroy()
    }

    companion object {
        fun join(a: String, b: String): String = when {
            a.isBlank() -> b
            b.isBlank() -> a
            else -> "$a $b"
        }

        /** The chip in the capture: what the instruction is about. */
        fun label(about: AskAbout): String = about.title.ifBlank {
            when (about) {
                is AskAbout.Event -> AppText.get(R.string.askui_about_event)
                is AskAbout.Task -> AppText.get(R.string.askui_about_task)
                is AskAbout.Recording -> AppText.get(R.string.askui_about_recording)
                is AskAbout.Conversation -> AppText.get(R.string.askui_about_conversation)
                is AskAbout.Approval -> AppText.get(R.string.askui_about_approval)
                is AskAbout.Note -> AppText.get(if (about.kind == "table") R.string.askui_about_table else R.string.askui_about_note)
            }
        }
    }
}
