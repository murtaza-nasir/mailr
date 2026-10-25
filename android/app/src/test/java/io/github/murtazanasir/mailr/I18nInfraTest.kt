package io.github.murtazanasir.mailr

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.i18n.AppLocales
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File
import java.time.LocalDate
import java.util.Locale

/** The language plumbing both apps share. */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class I18nInfraTest {
    private val supported = listOf("en", "de", "pt-BR")

    @Test fun theAccountLanguageMatchesWhatTheBuildHas() {
        assertEquals("de", AppLocales.match("de", supported))
        assertEquals("de", AppLocales.match("de-AT", supported))
        assertEquals("pt-BR", AppLocales.match("pt_br", supported))
        assertNull(AppLocales.match("fr", supported))
        assertNull(AppLocales.match("auto", supported))
        assertNull(AppLocales.match("", supported))
        assertNull(AppLocales.match(null, supported))
    }

    @Test fun chineseAndPortugueseTagsMatchTheShippedLanguages() {
        val shipped = listOf("en", "de", "es", "fr", "pt-BR", "ru", "zh-Hans")
        assertEquals("zh-Hans", AppLocales.match("zh-CN", shipped))
        assertEquals("zh-Hans", AppLocales.match("zh", shipped))
        assertNull(AppLocales.match("zh-TW", shipped))
        assertNull(AppLocales.match("zh-Hant-HK", shipped))
        assertEquals("pt-BR", AppLocales.match("pt", shipped))
        assertEquals("ru", AppLocales.match("ru-RU", shipped))
    }

    @Test fun theLanguageListMatchesBothLocaleConfigs() {
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        val listed = AppLocales.supported(ctx)
        assertEquals("en", listed.first())
        for (app in listOf("app", "notes")) {
            val xml = File("../$app/src/main/res/xml/locales_config.xml").readText()
            val names = Regex("""android:name="([^"]+)"""").findAll(xml).map { it.groupValues[1] }.toList()
            assertEquals("$app locales_config.xml", listed, names)
        }
    }

    @Test fun englishKeepsTheAppsOwnPatterns() {
        val d = LocalDate.of(2026, 10, 5)
        assertEquals("Oct 5", d.format(LocaleFormats.date("MMMd", "MMM d", Locale.US)))
        assertEquals("Mon 5 Oct", d.format(LocaleFormats.date("EEEdMMM", "EEE d MMM", Locale.UK)))
    }

    @Test fun otherLanguagesTakeTheirOwnOrder() {
        val d = LocalDate.of(2026, 10, 5)
        assertEquals("5. Okt.", d.format(LocaleFormats.date("MMMd", "MMM d", Locale.GERMAN)))
        assertEquals("1.234", LocaleFormats.number(1234, Locale.GERMAN))
        assertEquals("1,234", LocaleFormats.number(1234, Locale.US))
    }

    @Test fun appTextReadsResourcesWithoutTheAppsApplication() {
        assertEquals("Mailr", AppText.get(R.string.app_name))
    }
}
