package io.github.murtazanasir.mailr.i18n

import android.app.LocaleManager
import android.content.Context
import android.content.res.Configuration
import android.os.Build
import android.os.LocaleList
import io.github.murtazanasir.mailr.notes.R
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.util.Locale

/**
 * The app's language.
 *
 * - The mailr setting `language` (Settings → You → Region on the web, GET /me `settings.language`)
 *   comes first when it is set. [followAccount] applies it each time it changes on the server.
 * - Otherwise the app follows the phone: its system language, or the per-app language chosen in the
 *   phone's settings (Android 13 and later list the app there through `res/xml/locales_config.xml`).
 * - A per-app language chosen in the phone's settings after the account's language was applied wins
 *   until the account's language changes again.
 *
 * Android 13 and later keep the per-app language themselves ([LocaleManager], what
 * `AppCompatDelegate.setApplicationLocales` calls there). Before 13 the app keeps it in its own
 * preferences and every Activity applies it in `attachBaseContext` through [wrap].
 */
object AppLocales {
    private const val PREFS = "mailr_locale"
    /** The account language last applied, so a later GET /me applies it only when it changed. */
    private const val KEY_ACCOUNT = "account_tag"
    /** Before Android 13: the app's own language (null follows the phone). */
    private const val KEY_LEGACY = "legacy_tag"

    private val _changes = MutableStateFlow(0)
    /** Bumps when the app language changes before Android 13; open activities recreate themselves. */
    val changes: StateFlow<Int> get() = _changes

    /** Values of the setting that mean "follow the phone". */
    private val FOLLOW = setOf("", "auto", "system", "device")

    /** The languages this build has text for (`R.array.i18n_languages`, the same list as locales_config.xml). */
    fun supported(context: Context): List<String> = context.resources.getStringArray(R.array.i18n_languages).toList()

    /** Traditional Chinese (Taiwan, Hong Kong, Macau): never given Simplified Chinese. */
    private val TRADITIONAL = Regex("^zh-(hant|tw|hk|mo)\\b", RegexOption.IGNORE_CASE)

    /**
     * The supported language [tag] reads as, or null for "follow the phone" and for a language this
     * build has no text for ("pt-BR" matches "pt-BR", then "pt", then another "pt-…"; "zh-CN" matches
     * "zh-Hans"; "zh-TW" matches nothing), as on the web and the server.
     */
    fun match(tag: String?, supported: List<String>): String? {
        val t = tag?.trim()?.replace('_', '-') ?: return null
        if (t.lowercase() in FOLLOW) return null
        supported.firstOrNull { it.equals(t, ignoreCase = true) }?.let { return it }
        if (TRADITIONAL.containsMatchIn(t)) return null
        val lang = t.substringBefore('-')
        return supported.firstOrNull { it.equals(lang, ignoreCase = true) }
            ?: supported.firstOrNull { it.substringBefore('-').equals(lang, ignoreCase = true) }
    }

    /**
     * The account's `language` setting from GET /me. A new value is applied; the same value again
     * changes nothing, so a language picked in the phone's settings stays until the account's
     * language changes. Clearing the setting returns the app to the phone's language.
     */
    fun followAccount(context: Context, tag: String?) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val wanted = match(tag, supported(context))
        val before = prefs.getString(KEY_ACCOUNT, null)
        if (wanted == before) return
        prefs.edit().apply { if (wanted == null) remove(KEY_ACCOUNT) else putString(KEY_ACCOUNT, wanted) }.apply()
        set(context, wanted)
    }

    /** The app language now: [tag] null follows the phone. */
    fun set(context: Context, tag: String?) {
        if (Build.VERSION.SDK_INT >= 33) {
            context.getSystemService(LocaleManager::class.java)?.applicationLocales =
                if (tag == null) LocaleList.getEmptyLocaleList() else LocaleList.forLanguageTags(tag)
        } else {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .apply { if (tag == null) remove(KEY_LEGACY) else putString(KEY_LEGACY, tag) }.apply()
            legacy = tag
            legacyRead = true
            Locale.setDefault(tag?.let(Locale::forLanguageTag) ?: systemLocale())
            _changes.value++
        }
    }

    /** The app's own language before Android 13, or null. */
    @Volatile private var legacy: String? = null
    @Volatile private var legacyRead = false

    private fun legacyTag(context: Context): String? {
        if (!legacyRead) {
            legacy = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_LEGACY, null)
            legacyRead = true
        }
        return legacy
    }

    private fun systemLocale(): Locale = android.content.res.Resources.getSystem().configuration.locales[0]

    /**
     * [base] in the app's language: what an Activity passes to `super.attachBaseContext`, and the
     * context [AppText] reads. Android 13 and later already give every context the per-app language.
     */
    fun wrap(base: Context): Context {
        if (Build.VERSION.SDK_INT >= 33) return base
        val tag = legacyTag(base) ?: return base
        val locale = Locale.forLanguageTag(tag)
        if (base.resources.configuration.locales[0] == locale) return base
        Locale.setDefault(locale)
        val config = Configuration(base.resources.configuration).apply { setLocales(LocaleList(locale)) }
        return base.createConfigurationContext(config)
    }

    /** The locale text and dates are in now. */
    fun current(context: Context): Locale = wrap(context).resources.configuration.locales[0]
}
