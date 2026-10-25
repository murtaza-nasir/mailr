// ACT-SURFACES: android.settings
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.data.settings.AppLook
import io.github.murtazanasir.mailr.data.settings.ColourSource
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ServerPrefs
import io.github.murtazanasir.mailr.ui.theme.SeedScheme
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

/**
 * "Same as mailr on the web" (ACT settings.colours_follow_web): the phone builds the web's colours
 * from the same seed with the same rules (ui/theme/SeedScheme.kt). The cases are the web engine's
 * (frontend/src/lib/colour/cases.json, copied byte for byte into the test resources).
 */
class SeedSchemeTest {
    private val cases = Json.parseToJsonElement(javaClass.classLoader!!.getResource("colour_cases.json")!!.readText())
        .jsonObject["cases"]!!.jsonArray

    private fun channels(a: Int) = listOf((a shr 16) and 255, (a shr 8) and 255, a and 255)

    @Test fun theSharedCasesGiveTheWebsColours() {
        assertTrue(cases.size >= 40)
        var roles = 0
        var exact = 0
        for (el in cases) {
            val c = el.jsonObject
            val seed = c["seed"]!!.jsonPrimitive.content
            val harmony = SeedScheme.Harmony.of(c["harmony"]!!.jsonPrimitive.content)
            val r = SeedScheme.build(seed, harmony)
            for ((scheme, got) in listOf("light" to r.light, "dark" to r.dark)) {
                for ((role, want) in c[scheme]!!.jsonObject) {
                    val w = SeedScheme.hex(want.jsonPrimitive.content)
                    val g = got[role] ?: error("$seed $harmony $scheme: no $role")
                    roles++
                    if (w == g) exact++
                    // Java's and JavaScript's maths may differ in the last bit; a whole tone step is about 3 per channel.
                    val d = channels(w).zip(channels(g)).maxOf { (a, b) -> abs(a - b) }
                    assertTrue("$seed ${harmony.key} $scheme $role: web ${SeedScheme.hexOf(w)}, phone ${SeedScheme.hexOf(g)}", d <= 3)
                }
            }
        }
        assertTrue("only $exact of $roles roles match exactly", exact >= roles * 0.98)
    }

    @Test fun apcaMatchesThePublishedValues() {
        // apca-w3 (APCA-W3 0.0.98G-4g) examples, as in the web's engine.test.ts.
        fun lc(a: String, b: String) = SeedScheme.apca(SeedScheme.hex(a), SeedScheme.hex(b))
        assertEquals(63.056469930209424, lc("#888888", "#ffffff"), 1e-9)
        assertEquals(-68.54146436644962, lc("#ffffff", "#888888"), 1e-9)
        assertEquals(58.146262578561334, lc("#000000", "#aaaaaa"), 1e-9)
        assertEquals(-56.24113336839742, lc("#aaaaaa", "#000000"), 1e-9)
        assertEquals(91.66830811481631, lc("#112233", "#ddeeff"), 1e-9)
        assertEquals(-93.06770049484275, lc("#ddeeff", "#112233"), 1e-9)
        assertEquals(0.0, lc("#777777", "#777777"), 0.0)
    }

    @Test fun theOwnersMidBlueTakesWhiteTextInDark() {
        // ui_accent {seed #009ccd, complementary}: the dark primary stays at the seed and its text is white.
        val s = SeedScheme.colorScheme("#009ccd", SeedScheme.Harmony.COMPLEMENTARY, dark = true)
        assertEquals(0xFFFFFFFF.toInt(), s.onPrimary.toArgbInt())
        val tone = io.github.murtazanasir.mailr.ui.theme.hct.Hct.fromInt(s.primary.toArgbInt()).tone
        assertTrue("dark primary tone $tone", abs(tone - io.github.murtazanasir.mailr.ui.theme.hct.Hct.fromInt(SeedScheme.hex("#009ccd")).tone) < 1.5)
        assertTrue(Math.abs(SeedScheme.apca(s.onPrimary.toArgbInt(), s.primary.toArgbInt())) >= 60)
    }

    @Test fun everyPairPassesAaOnThePhonesSurfaces() {
        val fails = mutableListOf<String>()
        for (h in 0 until 360 step 20) for (c in listOf(0.0, 20.0, 50.0, 100.0)) for (t in listOf(15.0, 40.0, 65.0, 90.0)) {
            val seed = SeedScheme.hexOf(io.github.murtazanasir.mailr.ui.theme.hct.Hct.from(h.toDouble(), c, t).toInt())
            for (harmony in SeedScheme.Harmony.entries) for (dark in listOf(false, true)) {
                val s = SeedScheme.colorScheme(seed, harmony, dark)
                fun ratio(a: androidx.compose.ui.graphics.Color, b: androidx.compose.ui.graphics.Color) =
                    SeedScheme.contrast(a.toArgbInt(), b.toArgbInt())
                // Fills: APCA |Lc| 60 and WCAG 3:1, and dark text only where white would not read better.
                for ((name, fg, bg) in listOf(
                    Triple("onPrimary", s.onPrimary, s.primary), Triple("onSecondary", s.onSecondary, s.secondary),
                    Triple("onTertiary", s.onTertiary, s.tertiary),
                )) {
                    val (f, b) = fg.toArgbInt() to bg.toArgbInt()
                    val white = 0xFFFFFFFF.toInt()
                    val whiteBetter = f != white && Math.abs(SeedScheme.apca(white, b)) > Math.abs(SeedScheme.apca(f, b))
                    if (!SeedScheme.fillReads(f, b) || whiteBetter) {
                        fails += "$seed ${harmony.key} ${if (dark) "dark" else "light"} $name Lc ${"%.1f".format(SeedScheme.apca(f, b))}"
                    }
                }
                val pairs = listOf(
                    "onPrimaryContainer" to ratio(s.onPrimaryContainer, s.primaryContainer),
                    "onSecondaryContainer" to ratio(s.onSecondaryContainer, s.secondaryContainer),
                    "onTertiaryContainer" to ratio(s.onTertiaryContainer, s.tertiaryContainer),
                    "primary on surface" to ratio(s.primary, s.surface), "primary on background" to ratio(s.primary, s.background),
                )
                for ((name, r) in pairs) if (r < 4.5) fails += "$seed ${harmony.key} ${if (dark) "dark" else "light"} $name ${"%.2f".format(r)}"
            }
        }
        assertEquals(emptyList<String>(), fails)
    }

    private fun androidx.compose.ui.graphics.Color.toArgbInt(): Int =
        (0xFF shl 24) or (Math.round(red * 255) shl 16) or (Math.round(green * 255) shl 8) or Math.round(blue * 255)

    private fun settings(vararg kv: Pair<String, Any?>) = JsonObject(kv.associate { (k, v) ->
        k to when (v) {
            null -> JsonNull
            is String -> JsonPrimitive(v)
            is Map<*, *> -> JsonObject(v.entries.associate { (a, b) -> a as String to JsonPrimitive(b as String) })
            else -> error("unexpected $v")
        }
    })

    @Test fun theWebsChoiceResolvesAsTheWebDoes() {
        // Nothing chosen: the style's own colour.
        assertEquals("#1f6c86" to "single", ServerPrefs.webColours(settings("ui_theme" to "classic")))
        assertEquals("#6750a4" to "single", ServerPrefs.webColours(settings("ui_theme" to "cards", "ui_accent" to null)))
        // A preset in its own style; in another style, that style's own colour.
        val teal = mapOf("preset" to "cards.teal", "seed" to "#006a6a", "harmony" to "single")
        assertEquals("#006a6a" to "single", ServerPrefs.webColours(settings("ui_theme" to "cards", "ui_accent" to teal)))
        assertEquals("#17607a" to "single", ServerPrefs.webColours(settings("ui_theme" to "office", "ui_accent" to teal)))
        // A colour of your own, in every style.
        val own = mapOf("seed" to "#B04A7A", "harmony" to "complementary")
        assertEquals("#b04a7a" to "complementary", ServerPrefs.webColours(settings("ui_theme" to "desk", "ui_accent" to own)))
        // The old Cards string.
        assertEquals("#984061" to "single", ServerPrefs.webColours(settings("ui_theme" to "cards", "ui_accent" to "rose")))
        assertEquals("#1f6c86" to "single", ServerPrefs.webColours(settings("ui_accent" to "rose")))
        // Garbage: the style's own.
        assertEquals("#5151cd" to "single", ServerPrefs.webColours(settings("ui_theme" to "focus", "ui_accent" to mapOf("seed" to "red"))))
    }

    @Test fun thePhoneKeepsACopyAndUsesItOnlyWhenChosen() {
        val p = ServerPrefs.apply(Prefs(), settings("ui_theme" to "cards", "ui_accent" to mapOf("seed" to "#b04a7a", "harmony" to "triadic")))
        assertEquals("#b04a7a", p.webSeed)
        assertEquals("triadic", p.webHarmony)
        // The web look (the default) takes the web's colours until the user picks others.
        assertEquals(SeedScheme.Harmony.TRIADIC, p.webColours()!!.harmony)
        assertTrue(!p.dynamicColor)
        // Android's Material look takes the wallpaper's.
        val material = p.copy(look = AppLook.MATERIAL)
        assertNull(material.webColours())
        assertTrue(material.dynamicColor)
        // A pick of the user's own wins in either look.
        val web = material.copy(colourChoice = ColourSource.WEB)
        assertEquals(SeedScheme.Harmony.TRIADIC, web.webColours()!!.harmony)
        assertEquals(ColourSource.MAILR, p.copy(colourChoice = ColourSource.MAILR).colourSource)
        assertTrue(!web.dynamicColor)
    }
}
