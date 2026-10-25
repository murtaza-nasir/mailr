package io.github.murtazanasir.mailr

import android.app.Application
import io.github.murtazanasir.mailr.ui.util.Format
import io.github.murtazanasir.mailr.ui.util.LabelColourData
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Label colour schemes: the Kotlin table matches the shared JSON
 *  (the copy in test resources), and tokens and the legacy hex forms decode. */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class LabelColourDataTest {
    private val json = Json.parseToJsonElement(
        javaClass.classLoader!!.getResource("design/label-colours.json")!!.readText()).jsonObject

    @Test fun tableMatchesSharedJson() {
        assertEquals(json["shades"]!!.jsonPrimitive.int, LabelColourData.SHADES)
        assertEquals(json["hues"]!!.jsonArray.map { it.jsonPrimitive.content }, LabelColourData.HUES)
        val schemes = json["schemes"]!!.jsonArray
        assertEquals(schemes.size, LabelColourData.SCHEMES.size)
        schemes.zip(LabelColourData.SCHEMES).forEach { (js, ks) ->
            val s = js.jsonObject
            assertEquals(s["id"]!!.jsonPrimitive.content, ks.id)
            s["hues"]!!.jsonArray.zip(ks.hues).forEach { (jh, kh) ->
                val h = jh.jsonObject
                assertEquals(h["id"]!!.jsonPrimitive.content, kh.id)
                assertEquals(h["name"]!!.jsonPrimitive.content, kh.name)
                val light = h["light"]!!.jsonArray
                val dark = h["dark"]!!.jsonArray
                kh.shades.forEachIndexed { i, sh ->
                    fun v(a: kotlinx.serialization.json.JsonArray, k: String) = a[i].jsonObject[k]!!.jsonPrimitive.content
                    assertEquals(listOf(v(light, "bg"), v(light, "fg"), v(light, "dot"), v(dark, "bg"), v(dark, "fg"), v(dark, "dot")),
                        listOf(sh.bg, sh.fg, sh.dot, sh.darkBg, sh.darkFg, sh.darkDot))
                }
            }
        }
    }

    @Test fun tokensResolvePerTheme() {
        val teal = LabelColourData.SCHEMES.first { it.id == "default" }.hues.first { it.id == "teal" }.shades[2]
        val (bg, fg) = Format.labelColors("@default/teal/3")!!
        val (dbg, dfg) = Format.labelColors("@default/teal/3", dark = true)!!
        assertEquals(teal.bg, hex(bg)); assertEquals(teal.fg, hex(fg))
        assertEquals(teal.darkBg, hex(dbg)); assertEquals(teal.darkFg, hex(dfg))
        assertNotEquals(hex(bg), hex(dbg))
        assertEquals(teal.darkDot, hex(Format.labelDot("@default/teal/3", dark = true)!!))
        for (bad in listOf("@neon/teal/1", "@default/cyan/1", "@default/teal/5", "@default/teal/0")) assertNull(bad, Format.labelColors(bad))
    }

    @Test fun legacyHexStillDecodes() {
        val (bg, fg) = Format.labelColors("#fb4c2f,#ffffff", dark = true)!!
        assertEquals("#fb4c2f", hex(bg)); assertEquals("#ffffff", hex(fg))
        assertEquals("#16a766", hex(Format.labelColors("#16a766|#ffffff")!!.first))
        val (pale, text) = Format.labelColors("#fef1d1")!!
        assertEquals("#fef1d1", hex(pale)); assertTrue(text.red < 0.3f)
        assertEquals("#16a766", hex(Format.labelDot("#16a766,#ffffff")!!))
        assertNull(Format.labelColors(null))
    }

    private fun hex(c: androidx.compose.ui.graphics.Color): String {
        fun ch(v: Float) = "%02x".format(Math.round(v * 255))
        return "#" + ch(c.red) + ch(c.green) + ch(c.blue)
    }
}
