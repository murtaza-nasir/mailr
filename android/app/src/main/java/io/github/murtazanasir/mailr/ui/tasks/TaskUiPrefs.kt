package io.github.murtazanasir.mailr.ui.tasks

import io.github.murtazanasir.mailr.util.catching

import androidx.annotation.StringRes
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import android.content.Context
import android.content.SharedPreferences
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow

/** What a swipe on a task row does (complete, reschedule, delete or move). */
enum class SwipeAction(@StringRes val labelRes: Int) {
    COMPLETE(R.string.tasks_swipe_complete), RESCHEDULE(R.string.tasks_swipe_reschedule), DELETE(R.string.tasks_swipe_delete),
    MOVE(R.string.tasks_swipe_move);

    val label: String get() = AppText.get(labelRes)
}

/**
 * The Tasks screen's per-phone conveniences: the last view (the saved view) and
 * the swipe actions. Plain SharedPreferences; losing them only resets the screen.
 */
class TaskUiPrefs(context: Context) {
    private val sp = context.getSharedPreferences("tasks_ui", Context.MODE_PRIVATE)

    var view: TaskView?
        get() = TaskView.parse(sp.getString("view", null))
        set(v) { sp.edit().putString("view", v?.route).apply() }

    var swipeRight: SwipeAction
        get() = sp.getString("swipe_right", null)?.let { catching { SwipeAction.valueOf(it) }.getOrNull() } ?: SwipeAction.COMPLETE
        set(v) { sp.edit().putString("swipe_right", v.name).apply() }

    var swipeLeft: SwipeAction
        get() = sp.getString("swipe_left", null)?.let { catching { SwipeAction.valueOf(it) }.getOrNull() } ?: SwipeAction.RESCHEDULE
        set(v) { sp.edit().putString("swipe_left", v.name).apply() }

    /** The swipe actions (right, left), now and after every change (Settings or the Tasks ⋮ menu). */
    fun swipes(): Flow<Pair<SwipeAction, SwipeAction>> = callbackFlow {
        trySend(swipeRight to swipeLeft)
        val l = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
            if (key == "swipe_right" || key == "swipe_left") trySend(swipeRight to swipeLeft)
        }
        sp.registerOnSharedPreferenceChangeListener(l)
        awaitClose { sp.unregisterOnSharedPreferenceChangeListener(l) }
    }
}
