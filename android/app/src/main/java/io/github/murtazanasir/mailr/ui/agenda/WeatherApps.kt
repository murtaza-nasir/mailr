package io.github.murtazanasir.mailr.ui.agenda

import android.app.Activity
import android.app.SearchManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.Toast
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.weather.WeatherStore

/**
 * The apps the widget's weather can open. Android has a "weather app" category
 * (`android.intent.category.APP_WEATHER`, Android 12 and later) that few apps declare yet, so the
 * suggestions are the apps in that category and the apps whose name or package says weather; any
 * other app can be picked from the full list.
 */
object WeatherApps {
    const val CATEGORY_APP_WEATHER = "android.intent.category.APP_WEATHER"

    data class App(val component: String, val label: String)

    /** Every app with a launcher icon, by name. */
    fun all(context: Context): List<App> {
        val pm = context.packageManager
        return pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0)
            .filter { it.activityInfo.packageName != context.packageName }
            .map { App(ComponentName(it.activityInfo.packageName, it.activityInfo.name).flattenToString(), it.loadLabel(pm).toString()) }
            .distinctBy { it.component }
            .sortedBy { it.label.lowercase() }
    }

    /** The apps that look like weather apps: the APP_WEATHER category, or "weather" in the name or package. */
    fun suggested(context: Context): List<App> {
        val pm = context.packageManager
        val declared = pm.queryIntentActivities(Intent.makeMainSelectorActivity(Intent.ACTION_MAIN, CATEGORY_APP_WEATHER), 0)
            .map { it.activityInfo.packageName }.toSet()
        val word = context.getString(R.string.agenda_weather_word).lowercase()
        return all(context).filter { a ->
            val pkg = a.component.substringBefore('/')
            pkg in declared || looksLikeWeather(a.label, pkg, word)
        }
    }

    fun looksLikeWeather(label: String, pkg: String, word: String): Boolean {
        val l = label.lowercase()
        val p = pkg.lowercase()
        return "weather" in l || "weather" in p || (word.isNotBlank() && word in l)
    }

    /**
     * What a tap on the weather opens: the chosen app when it is still installed; otherwise Android's
     * own choice among weather apps; otherwise a web search for the weather.
     */
    fun intent(context: Context, chosen: String?): Intent {
        val pm = context.packageManager
        chosen?.let(ComponentName::unflattenFromString)?.let { cn ->
            val i = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER).setComponent(cn)
            if (pm.resolveActivity(i, 0) != null) return i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            pm.getLaunchIntentForPackage(cn.packageName)?.let { return it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK) }
        }
        val selector = Intent.makeMainSelectorActivity(Intent.ACTION_MAIN, CATEGORY_APP_WEATHER)
        if (pm.queryIntentActivities(selector, PackageManager.MATCH_DEFAULT_ONLY).isNotEmpty()) return selector.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val search = Intent(Intent.ACTION_WEB_SEARCH).putExtra(SearchManager.QUERY, context.getString(R.string.agenda_weather_word))
        return Intent.createChooser(search, null).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }
}

/**
 * The widget's weather tap lands here (an explicit, private activity, so the widget never holds an
 * intent for another app): it opens the chosen weather app, or Android's choice, and closes.
 */
class WeatherOpenActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        try {
            startActivity(WeatherApps.intent(this, WeatherStore(this).app))
        } catch (_: android.content.ActivityNotFoundException) {
            Toast.makeText(this, getString(R.string.agenda_no_weather_app), Toast.LENGTH_SHORT).show()
        }
        finish()
    }
}
