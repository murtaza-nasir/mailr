package io.github.murtazanasir.mailr.i18n

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import androidx.annotation.PluralsRes
import androidx.annotation.StringRes

/**
 * Strings for code that runs without a Compose scope or an Activity at hand: notifications, workers,
 * view models, error messages ([io.github.murtazanasir.mailr.data.api.userMessage]) and formatters. Compose code
 * uses `stringResource` and `pluralStringResource` instead.
 *
 * Both Application classes call [init] first thing. Under Robolectric a test that never made the
 * app's Application still works: the context then comes from `ApplicationProvider`. A plain JVM test
 * cannot read resources at all; run it with Robolectric.
 *
 * The context follows the app's language: on Android 13 and later the system gives the application
 * its per-app locale; before 13, [AppLocales.wrap] applies the language the app chose.
 */
object AppText {
    @Volatile private var app: Context? = null

    fun init(context: Context) {
        app = context.applicationContext ?: context
    }

    /** The application context in the app's language. */
    val context: Context
        get() = AppLocales.wrap(fromTestRunner() ?: app ?: error(
            "AppText is not initialised: the Application calls AppText.init, and a JVM test that reads strings runs with Robolectric"))

    fun get(@StringRes id: Int): String = context.getString(id)

    fun get(@StringRes id: Int, vararg args: Any): String = context.getString(id, *args)

    /** A count in the language's plural form: `plural(R.plurals.x, n, n)` for "%d items". */
    fun plural(@PluralsRes id: Int, count: Int, vararg args: Any): String =
        context.resources.getQuantityString(id, count, *args)

    /**
     * Robolectric's application, found by name so the app does not depend on the test library. It is
     * looked up on every call and never kept: each test has an Application of its own, and an earlier
     * test's would carry its configuration into the next one.
     */
    private val testRunner: java.lang.reflect.Method? by lazy {
        catching { Class.forName("androidx.test.core.app.ApplicationProvider").getMethod("getApplicationContext") }.getOrNull()
    }

    private fun fromTestRunner(): Context? = catching { testRunner?.invoke(null) as? Context }.getOrNull()
}
