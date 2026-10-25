package io.github.murtazanasir.mailr.notesapp.data

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.notesapp.R
import io.github.murtazanasir.mailr.i18n.AppText
import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Light, dark, or as the phone is set. */
enum class ThemeChoice(@androidx.annotation.StringRes val labelRes: Int) {
    SYSTEM(R.string.notesapp_theme_system), LIGHT(R.string.notesapp_theme_light), DARK(R.string.notesapp_theme_dark);

    val label: String get() = AppText.get(labelRes)
}

/** What a widget shows: a list, a note or a table ([id]), and for a table the columns it shows. */
data class WidgetTarget(val kind: String, val id: String, val columns: List<String> = emptyList()) {
    fun encode(): String = listOf(kind, id, columns.joinToString(",")).joinToString("\t")

    companion object {
        fun decode(s: String?): WidgetTarget? {
            val p = s?.split('\t') ?: return null
            if (p.size < 2 || p[1].isBlank()) return null
            return WidgetTarget(p[0], p[1], p.getOrNull(2)?.split(',')?.filter { it.isNotBlank() }.orEmpty())
        }
    }
}

/**
 * The Notes app's own settings on this phone: the theme, the recently opened notes (the launcher's
 * dynamic shortcuts and the share sheet's "recent lists first"), what each widget shows and the
 * capture widget's default list. Nothing here is account data; sign-out clears the account parts.
 */
class NotesPrefs(context: Context) {
    private val prefs = context.getSharedPreferences("notes_app", Context.MODE_PRIVATE)

    data class Look(val theme: ThemeChoice, val dynamicColour: Boolean)

    private val _look = MutableStateFlow(readLook())
    val look: StateFlow<Look> = _look.asStateFlow()

    private fun readLook() = Look(
        catching { ThemeChoice.valueOf(prefs.getString("theme", null) ?: "SYSTEM") }.getOrDefault(ThemeChoice.SYSTEM),
        prefs.getBoolean("dynamic_colour", false),
    )

    fun setLook(l: Look) {
        prefs.edit().putString("theme", l.theme.name).putBoolean("dynamic_colour", l.dynamicColour).apply()
        _look.value = l
    }

    private val _hideContent = MutableStateFlow(prefs.getBoolean("hide_content", false))

    /** "Hide in Recents and screenshots" (the mailr app's privacy setting): FLAG_SECURE on every screen. */
    val hideContent: StateFlow<Boolean> = _hideContent.asStateFlow()

    fun setHideContent(on: Boolean) {
        prefs.edit().putBoolean("hide_content", on).apply()
        _hideContent.value = on
    }

    /** "note:<id>" or "table:<id>", newest first. */
    fun recents(): List<String> = prefs.getString("recents", "").orEmpty().split('\n').filter { it.isNotBlank() }

    /** Remember an opened note or table; true when the list changed. */
    fun opened(kind: String, id: String): Boolean {
        if (id.startsWith("local-")) return false
        val entry = "$kind:$id"
        val before = recents()
        val after = (listOf(entry) + before.filter { it != entry }).take(MAX_RECENTS)
        if (after == before) return false
        prefs.edit().putString("recents", after.joinToString("\n")).apply()
        return true
    }

    fun forget(id: String) {
        prefs.edit().putString("recents", recents().filter { it.substringAfter(':') != id }.joinToString("\n")).apply()
    }

    fun widget(appWidgetId: Int): WidgetTarget? = WidgetTarget.decode(prefs.getString("widget_$appWidgetId", null))

    fun setWidget(appWidgetId: Int, t: WidgetTarget?) {
        prefs.edit().apply { if (t == null) remove("widget_$appWidgetId") else putString("widget_$appWidgetId", t.encode()) }.apply()
    }

    /** The list "Add to <list>" adds to (the capture widget), or null. */
    var defaultList: String?
        get() = prefs.getString("default_list", null)
        set(v) { prefs.edit().apply { if (v == null) remove("default_list") else putString("default_list", v) }.apply() }

    /** Sign-out: the recent notes and the default list belong to the account. Widgets keep their slot, now empty. */
    fun forgetAccount() {
        prefs.edit().remove("recents").remove("default_list").apply()
    }

    companion object {
        const val MAX_RECENTS = 10
    }
}
