package io.github.murtazanasir.mailr.ui.ask

import android.app.Activity
import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.ui.MainActivity

/** An instruction for the assistant from the ask capture: the item it is about and the words. */
data class AskRequest(val about: AskAbout?, val text: String?)

/**
 * Intents into the ask capture (AskActivity) and out of it into the app.
 *
 * - Into the capture: the item travels as extras (kind, ids, title). No words travel, so an
 *   outside intent can at most open the capture.
 * - Out of the capture: the request waits in AppGraph.pendingAsk, and MainActivity only comes to
 *   the front ([ACTION_OPEN_ASSISTANT]); Home moves it into the Assistant tab.
 * - The launcher shortcuts (res/xml/shortcuts.xml) use [ACTION_ASK] and [ACTION_DICTATE].
 *
 * ACT agent.dictate @android.launcher
 */
object AskIntents {
    const val ACTION_ASK = "io.github.murtazanasir.mailr.action.ASK"
    /** The launcher shortcut and the widget's microphone: the capture starts listening. */
    const val ACTION_DICTATE = "io.github.murtazanasir.mailr.action.DICTATE"
    const val ACTION_OPEN_ASSISTANT = "io.github.murtazanasir.mailr.action.OPEN_ASSISTANT"

    const val EXTRA_KIND = "ask_kind"
    const val EXTRA_UID = "ask_uid"
    const val EXTRA_OCCURRENCE = "ask_occurrence"
    const val EXTRA_TITLE = "ask_title"
    const val EXTRA_CONNECTOR = "ask_connector"
    const val EXTRA_RECORDING = "ask_recording"
    const val EXTRA_CONVERSATION = "ask_conversation"
    const val EXTRA_APPROVAL = "ask_approval"
    /** true: listen at once; false: the keyboard; absent: the user's setting. */
    const val EXTRA_LISTEN = "ask_listen"
    /** Where the capture was opened from (widget, tile, shortcut, notification, sheet, …); for tests and logs. */
    const val EXTRA_FROM = "ask_from"

    fun capture(context: Context, about: AskAbout?, listen: Boolean? = null, from: String): Intent =
        Intent(context, AskActivity::class.java).setAction(if (listen == true) ACTION_DICTATE else ACTION_ASK)
            .putExtra(EXTRA_FROM, from)
            .apply {
                listen?.let { putExtra(EXTRA_LISTEN, it) }
                put(this, about)
            }
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)

    fun put(i: Intent, about: AskAbout?) {
        when (about) {
            is AskAbout.Task -> i.putExtra(EXTRA_KIND, "task").putExtra(EXTRA_UID, about.uid)
            is AskAbout.Event -> i.putExtra(EXTRA_KIND, "event").putExtra(EXTRA_UID, about.uid).putExtra(EXTRA_OCCURRENCE, about.occurrence)
            is AskAbout.Recording -> i.putExtra(EXTRA_KIND, "recording").putExtra(EXTRA_CONNECTOR, about.connectorId)
                .putExtra(EXTRA_RECORDING, about.recordingId)
            is AskAbout.Conversation -> i.putExtra(EXTRA_KIND, "conversation").putExtra(EXTRA_CONVERSATION, about.id)
            is AskAbout.Approval -> i.putExtra(EXTRA_KIND, "approval").putExtra(EXTRA_APPROVAL, about.id)
            is AskAbout.Note -> i.putExtra(EXTRA_KIND, "note").putExtra(EXTRA_UID, about.id).putExtra(EXTRA_OCCURRENCE, about.kind)
            null -> return
        }
        i.putExtra(EXTRA_TITLE, about.title)
    }

    fun read(i: Intent?): AskAbout? {
        i ?: return null
        val title = i.getStringExtra(EXTRA_TITLE).orEmpty()
        return when (i.getStringExtra(EXTRA_KIND)) {
            "task" -> i.getStringExtra(EXTRA_UID)?.takeIf { it.isNotBlank() }?.let { AskAbout.Task(it, title) }
            "event" -> i.getStringExtra(EXTRA_UID)?.takeIf { it.isNotBlank() }?.let {
                AskAbout.Event(it, i.getStringExtra(EXTRA_OCCURRENCE)?.ifBlank { null }, title)
            }
            "recording" -> {
                val c = i.getLongExtra(EXTRA_CONNECTOR, 0)
                val r = i.getLongExtra(EXTRA_RECORDING, 0)
                if (c > 0 && r > 0) AskAbout.Recording(c, r, title) else null
            }
            "conversation" -> i.getLongExtra(EXTRA_CONVERSATION, 0).takeIf { it != 0L }?.let { AskAbout.Conversation(it, title) }
            "approval" -> i.getLongExtra(EXTRA_APPROVAL, 0).takeIf { it > 0L }?.let { AskAbout.Approval(it, title) }
            "note" -> i.getStringExtra(EXTRA_UID)?.takeIf { it.isNotBlank() }?.let {
                AskAbout.Note(it, title, i.getStringExtra(EXTRA_OCCURRENCE) ?: "note")
            }
            else -> null
        }
    }

    /** null: the user's setting decides. */
    fun listen(i: Intent?): Boolean? = when {
        i == null -> null
        i.hasExtra(EXTRA_LISTEN) -> i.getBooleanExtra(EXTRA_LISTEN, false)
        i.action == ACTION_DICTATE -> true
        else -> null
    }

    /** The app, brought to the front on its Assistant tab (the request is in AppGraph.pendingAsk). */
    fun openAssistant(context: Context): Intent = Intent(context, MainActivity::class.java).setAction(ACTION_OPEN_ASSISTANT)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)

    /**
     * Starts [intent] once the phone is unlocked. Over the lock screen (the reminder sheet and the
     * full-screen alert show there) the user is asked to unlock first; the capture never shows
     * over the lock screen.
     */
    fun startUnlocked(context: Context, intent: Intent) {
        val km = context.getSystemService(KeyguardManager::class.java)
        val activity = context as? Activity
        if (activity != null && km?.isKeyguardLocked == true && Build.VERSION.SDK_INT >= 26) {
            km.requestDismissKeyguard(activity, object : KeyguardManager.KeyguardDismissCallback() {
                override fun onDismissSucceeded() { context.startActivity(intent) }
            })
        } else context.startActivity(intent)
    }
}
