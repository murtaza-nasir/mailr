package io.github.murtazanasir.mailr.ui.ask

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.ColorFilter
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.GlanceTheme
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.SizeMode
import androidx.glance.LocalSize
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.height
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.material3.ColorProviders
import androidx.glance.semantics.contentDescription
import androidx.glance.semantics.semantics
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.ui.tasks.ShareTaskActivity
import io.github.murtazanasir.mailr.ui.theme.Dark
import io.github.murtazanasir.mailr.ui.theme.Light

/**
 * "Talk to Ambrose" on the home screen (android.widget): one
 * tap opens Ambrose already listening. It replaced the earlier "Ask mailr" bar under the
 * same receiver, so widgets placed before keep working: at 1x1 it is a round microphone with
 * "Ambrose" under it; wider (an old 4x1 bar), a pill with the words. Typing is one tap away in
 * the capture itself. The Today widget (ui/today) carries the same button.
 *
 * ACT agent.dictate @android.widget @android.launcher
 */
class AskWidget : GlanceAppWidget() {
    override val sizeMode: SizeMode = SizeMode.Exact

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        provideContent { AskWidgetContent(context) }
    }
}

class AskWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = AskWidget()
}

/** mailr's own colours, light and dark by the launcher's mode (not the wallpaper's). */
val AskWidgetColors = ColorProviders(light = Light, dark = Dark)

@Composable
fun AskWidgetContent(context: Context) {
    val listen = actionStartActivity(AskIntents.capture(context, null, listen = true, from = "widget"))
    val size = LocalSize.current
    // The user's name for the assistant (AssistantName); the picker's label stays the default.
    val name = io.github.murtazanasir.mailr.i18n.AssistantName.now()
    val talk = AssistantSurfaces.talkLabel(name)
    GlanceTheme(AskWidgetColors) {
        if (size.width >= 160.dp) {
            Row(GlanceModifier.fillMaxSize().background(ImageProvider(R.drawable.widget_pill), colorFilter = ColorFilter.tint(GlanceTheme.colors.primary)).clickable(listen)
                .padding(horizontal = 18.dp).semantics { contentDescription = talk },
                verticalAlignment = Alignment.CenterVertically, horizontalAlignment = Alignment.CenterHorizontally) {
                Image(ImageProvider(R.drawable.ic_ask_mic), null, GlanceModifier.size(24.dp),
                    colorFilter = ColorFilter.tint(GlanceTheme.colors.onPrimary))
                Spacer(GlanceModifier.width(10.dp))
                Text(talk, maxLines = 1,
                    style = TextStyle(color = GlanceTheme.colors.onPrimary, fontSize = 16.sp, fontWeight = FontWeight.Medium))
            }
        } else {
            Column(GlanceModifier.fillMaxSize().background(ImageProvider(R.drawable.widget_bg), colorFilter = ColorFilter.tint(GlanceTheme.colors.surface)).clickable(listen)
                .padding(4.dp).semantics { contentDescription = talk },
                verticalAlignment = Alignment.CenterVertically, horizontalAlignment = Alignment.CenterHorizontally) {
                Box(GlanceModifier.size(48.dp).background(ImageProvider(R.drawable.widget_circle), colorFilter = ColorFilter.tint(GlanceTheme.colors.primary)), contentAlignment = Alignment.Center) {
                    Image(ImageProvider(R.drawable.ic_ask_mic), null, GlanceModifier.size(26.dp),
                        colorFilter = ColorFilter.tint(GlanceTheme.colors.onPrimary))
                }
                if (size.height >= 76.dp) {
                    Spacer(GlanceModifier.height(4.dp))
                    Text(name, maxLines = 1,
                        style = TextStyle(color = GlanceTheme.colors.onSurface, fontSize = 12.sp, fontWeight = FontWeight.Medium))
                }
            }
        }
    }
}

/**
 * "Talk to Ambrose" in Quick Settings: the capture, listening at once. Over the lock screen
 * the phone asks to be unlocked first.
 *
 * ACT agent.dictate
 */
class AskTileService : TileService() {
    override fun onStartListening() {
        qsTile?.apply {
            state = Tile.STATE_INACTIVE
            // The manifest's label is the default name; the tile itself shows the user's.
            label = AssistantSurfaces.talkLabel()
            updateTile()
        }
    }

    override fun onClick() {
        val open = { startCollapsed(AskIntents.capture(this, null, listen = true, from = "tile")) }
        if (isLocked) unlockAndRun { open() } else open()
    }

}

/**
 * "New task" in Quick Settings: the task editor, empty, over whatever is on screen (the
 * share target's editor, so Save goes back to where the tile was pulled down).
 *
 * ACT task.create @android.launcher
 */
class NewTaskTileService : TileService() {
    override fun onStartListening() {
        qsTile?.apply {
            state = Tile.STATE_INACTIVE
            label = getString(R.string.new_task_tile_label)
            updateTile()
        }
    }

    override fun onClick() {
        val i = Intent(this, ShareTaskActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
        if (isLocked) unlockAndRun { startCollapsed(i) } else startCollapsed(i)
    }
}

/** Starts [i] from a tile and closes the shade (PendingIntent from Android 14). */
@android.annotation.SuppressLint("StartActivityAndCollapseDeprecated")  // the Intent form only runs below Android 14
internal fun TileService.startCollapsed(i: Intent) {
    if (Build.VERSION.SDK_INT >= 34) {
        startActivityAndCollapse(PendingIntent.getActivity(this, i.component?.className.hashCode(), i,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
    } else @Suppress("DEPRECATION") {
        startActivityAndCollapse(i)
    }
}
