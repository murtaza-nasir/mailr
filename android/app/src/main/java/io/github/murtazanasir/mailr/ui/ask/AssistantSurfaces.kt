package io.github.murtazanasir.mailr.ui.ask

import io.github.murtazanasir.mailr.util.catching

import android.content.ComponentName
import android.content.Context
import android.service.quicksettings.TileService
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.drawable.IconCompat
import androidx.glance.appwidget.updateAll
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.AssistantName

/**
 * The places outside the app that name the assistant: the launcher shortcuts (Ask, Dictate), the
 * Quick Settings tile and the Talk widget. Each takes the user's name for the assistant
 * ([AssistantName]); [refresh] runs at start and whenever the name changes.
 *
 * The launcher shortcuts are dynamic (a static one in res/xml cannot change its label). What only the
 * manifest can give stays with the default name: the widget's name and description in the widget
 * picker, and the tile's name in the phone's list of tiles before it is added.
 */
object AssistantSurfaces {
    /** Dynamic ids: the static "ask" and "dictate" of older versions were manifest shortcuts. */
    const val ASK_ID = "ask-assistant"
    const val DICTATE_ID = "dictate-assistant"

    data class ShortcutLabels(val short: String, val long: String)

    /** The labels of the Ask and Dictate shortcuts for [name]. */
    fun askLabels(name: String = AssistantName.now()) =
        ShortcutLabels(AppText.get(R.string.ask_shortcut_short, name), AppText.get(R.string.ask_shortcut_long, name))

    fun dictateLabels(name: String = AssistantName.now()) =
        ShortcutLabels(AppText.get(R.string.dictate_shortcut_short), AppText.get(R.string.dictate_shortcut_long, name))

    /** "Talk to Ambrose": the tile's label and the Talk widget's words. */
    fun talkLabel(name: String = AssistantName.now()): String = AppText.get(R.string.today_talk, name)

    fun shortcuts(context: Context, name: String = AssistantName.now()): List<ShortcutInfoCompat> {
        val ask = askLabels(name)
        val dictate = dictateLabels(name)
        return listOf(
            ShortcutInfoCompat.Builder(context, ASK_ID)
                .setShortLabel(ask.short.take(25)).setLongLabel(ask.long.take(60))
                .setIcon(IconCompat.createWithResource(context, R.drawable.ic_shortcut_ask))
                .setIntent(AskIntents.capture(context, null, listen = false, from = "shortcut"))
                .setRank(0).build(),
            ShortcutInfoCompat.Builder(context, DICTATE_ID)
                .setShortLabel(dictate.short.take(25)).setLongLabel(dictate.long.take(60))
                .setIcon(IconCompat.createWithResource(context, R.drawable.ic_shortcut_mic))
                .setIntent(AskIntents.capture(context, null, listen = true, from = "shortcut"))
                .setRank(1).build(),
        )
    }

    /** Publish the shortcuts with the current name (an existing one is updated, pinned copies too). */
    fun publishShortcuts(context: Context) {
        shortcuts(context).forEach { sc -> catching { ShortcutManagerCompat.pushDynamicShortcut(context, sc) } }
    }

    /** The name changed (or the app started): shortcuts, the tile and the widgets follow. */
    suspend fun refresh(context: Context) {
        publishShortcuts(context)
        catching { TileService.requestListeningState(context, ComponentName(context, AskTileService::class.java)) }
        try {
            AskWidget().updateAll(context)
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (_: Exception) {
            // No Talk widget placed: nothing to draw.
        }
        io.github.murtazanasir.mailr.ui.today.TodayWidgets.refresh(context)
        io.github.murtazanasir.mailr.ui.agenda.AgendaWidgets.refresh(context)
    }
}
