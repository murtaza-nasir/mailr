package io.github.murtazanasir.mailr.ui.theme

import androidx.compose.material3.ColorScheme
import androidx.compose.ui.graphics.Color
import io.github.murtazanasir.mailr.ui.theme.hct.Hct
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow

/**
 * The web's colour engine (frontend/src/lib/colour/engine.ts), line for line: one seed colour and a
 * harmony mode give light and dark accent colours. Text on a filled colour (primary, secondary,
 * tertiary, the FAB, the solid fill, the bar) is white or the fill's dark partner, whichever APCA
 * rates higher, and the fill moves until |Lc| 60 and WCAG 3:1; every other pair is moved until it
 * passes WCAG AA. HCT is Google's (the `hct` package beside this file is the Java port of
 * material-color-utilities, Apache License 2.0, with only the package name changed). The shared
 * cases (frontend/src/lib/colour/cases.json) run against both in SeedSchemeTest.
 */
object SeedScheme {
    enum class Harmony(val key: String) {
        SINGLE("single"), COMPLEMENTARY("complementary"), ANALOGOUS("analogous"), TRIADIC("triadic"), SPLIT("split");

        companion object {
            fun of(key: String?): Harmony = entries.firstOrNull { it.key == key } ?: SINGLE
        }
    }

    data class HueChroma(val hue: Double, val chroma: Double)

    /** What sits under the accents: surfaces accent text is drawn on, body text, the toast. */
    data class Context(val surfaces: List<Int>, val text: Int, val toast: Int)

    private const val AA_TEXT = 4.5
    private const val AA_UI = 3.0
    /** APCA's level for small bold UI text on a fill. */
    const val APCA_FILL = 60.0
    private const val SELECTION_CHROMA = 24.0
    private const val CONTAINER_CHROMA = 32.0
    private const val WHITE = 0xFFFFFFFF.toInt()

    /** The web's neutral context (engine.ts NEUTRAL_CONTEXT), for the shared cases. */
    val NEUTRAL_LIGHT = Context(listOf(hex("#ffffff"), hex("#f8f9fa")), hex("#1f1f1f"), hex("#303030"))
    val NEUTRAL_DARK = Context(listOf(hex("#1b1b1b"), hex("#282828")), hex("#e3e3e3"), hex("#e3e3e3"))

    fun hex(s: String): Int = (0xFF000000 or s.trim().removePrefix("#").toLong(16)).toInt()
    fun hexOf(argb: Int): String = "#" + (argb and 0xFFFFFF).toString(16).padStart(6, '0')
    fun isHex(s: String?): Boolean = s != null && Regex("#[0-9a-fA-F]{6}").matches(s)

    private fun lin(c: Int): Double { val s = c / 255.0; return if (s <= 0.04045) s / 12.92 else ((s + 0.055) / 1.055).pow(2.4) }
    private fun luminance(argb: Int) = 0.2126 * lin((argb shr 16) and 255) + 0.7152 * lin((argb shr 8) and 255) + 0.0722 * lin(argb and 255)

    /** WCAG 2 contrast ratio, 1 to 21. */
    fun contrast(a: Int, b: Int): Double {
        val la = luminance(a)
        val lb = luminance(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
    }

    // APCA-W3 0.0.98G-4g (engine.ts / contrast.ts `apca`): screen luminance on a plain 2.4 curve.
    private fun apcaY(argb: Int): Double {
        fun ch(c: Int) = (c / 255.0).pow(2.4)
        return 0.2126729 * ch((argb shr 16) and 255) + 0.7151522 * ch((argb shr 8) and 255) + 0.072175 * ch(argb and 255)
    }

    /** APCA lightness contrast Lc of text [fg] on background [bg]: positive for dark text on light. */
    fun apca(fg: Int, bg: Int): Double {
        fun clamp(y: Double) = if (y > 0.022) y else y + (0.022 - y).pow(1.414)
        val t = clamp(apcaY(fg))
        val b = clamp(apcaY(bg))
        if (Math.abs(b - t) < 0.0005) return 0.0
        return if (b > t) {
            val sapc = (b.pow(0.56) - t.pow(0.57)) * 1.14
            if (sapc < 0.1) 0.0 else (sapc - 0.027) * 100
        } else {
            val sapc = (b.pow(0.65) - t.pow(0.62)) * 1.14
            if (sapc > -0.1) 0.0 else (sapc + 0.027) * 100
        }
    }

    /** Text on a fill reads at |Lc| 60 and WCAG 3:1. */
    fun fillReads(text: Int, fill: Int) = Math.abs(apca(text, fill)) >= APCA_FILL && contrast(text, fill) >= AA_UI

    private fun wrap(h: Double): Double { val x = h % 360; return if (x < 0) x + 360 else x }

    fun harmonySeeds(p: HueChroma, h: Harmony): Pair<HueChroma, HueChroma> {
        val placed = max(24.0, min(p.chroma, 64.0))
        return when (h) {
            Harmony.SINGLE -> HueChroma(p.hue, 16.0) to HueChroma(wrap(p.hue + 60), 24.0)
            Harmony.COMPLEMENTARY -> HueChroma(p.hue, 16.0) to HueChroma(wrap(p.hue + 180), placed)
            Harmony.ANALOGOUS -> HueChroma(wrap(p.hue + 30), placed) to HueChroma(wrap(p.hue - 30), placed)
            Harmony.TRIADIC -> HueChroma(wrap(p.hue + 120), placed) to HueChroma(wrap(p.hue - 120), placed)
            Harmony.SPLIT -> HueChroma(wrap(p.hue + 150), placed) to HueChroma(wrap(p.hue + 210), placed)
        }
    }

    private fun tone(p: HueChroma, t: Int): Int = Hct.from(p.hue, p.chroma, t.coerceIn(0, 100).toDouble()).toInt()
    private fun soft(p: HueChroma) = HueChroma(p.hue, min(p.chroma, CONTAINER_CHROMA))
    private fun jsRound(x: Double): Int = Math.floor(x + 0.5).toInt()

    private fun push(p: HueChroma, t: Int, dir: Int, ok: (Int) -> Boolean): Int {
        var x = t
        while (!ok(tone(p, x)) && x + dir in 0..100) x += dir
        return x
    }

    private fun pair(fill: HueChroma, fillT: Int, fg: HueChroma, fgT: Int, dir: Int): Pair<Int, Int> {
        fun ok(f: Int, g: Int) = contrast(tone(fill, f), tone(fg, g)) >= AA_TEXT
        var g = fgT
        while (!ok(fillT, g) && g + dir in 0..100) g += dir
        var f = fillT
        while (!ok(f, g) && f - dir in 0..100) f -= dir
        return tone(fill, f) to tone(fg, g)
    }

    /** A fill and its text (engine.ts `fill`): white or [dark], whichever APCA rates higher at tone [t];
     *  the fill moves away from the text until it reads, while [keep] holds; else the other text. */
    private fun fill(p: HueChroma, t: Int, dark: Int, keep: (Int) -> Boolean = { true }, start: Int = tone(p, t)): Pair<Int, Int> {
        fun at(x: Int) = if (x == t) start else tone(p, x)
        val texts = if (Math.abs(apca(WHITE, start)) >= Math.abs(apca(dark, start))) listOf(WHITE, dark) else listOf(dark, WHITE)
        for (text in texts) {
            val dir = if (text == WHITE) -1 else 1
            var x = t
            while (x in 0..100) {
                if (!keep(at(x))) break
                if (fillReads(text, at(x))) return at(x) to text
                x += dir
            }
        }
        val text = texts[0]
        val dir = if (text == WHITE) -1 else 1
        var x = t
        while (!fillReads(text, at(x)) && x + dir in 0..100) x += dir
        return at(x) to text
    }

    private fun onSurfaces(c: Int, ctx: Context) = ctx.surfaces.all { contrast(c, it) >= AA_TEXT }

    /** The tokens, by the web's role names (engine.ts ROLES). */
    class Result(val light: Map<String, Int>, val dark: Map<String, Int>)

    fun build(seedHex: String, harmony: Harmony, light: Context = NEUTRAL_LIGHT, dark: Context = NEUTRAL_DARK): Result {
        val seed = hex(seedHex)
        val s = Hct.fromInt(seed)
        val limited = s.chroma >= 16 && Hct.from(s.hue, s.chroma + 10, s.tone).chroma - s.chroma < 1
        val p = HueChroma(s.hue, if (limited) max(s.chroma, 48.0) else s.chroma)
        val (sec, ter) = harmonySeeds(p, harmony)
        val t0 = jsRound(max(20.0, min(60.0, s.tone)))
        val l = buildLight(p, sec, ter, t0, seed, s.tone, light)
        val d = buildDark(p, sec, ter, t0, l.getValue("primary") to l.getValue("onPrimary"), dark)
        return Result(l, d)
    }

    private fun roles(
        pr: Pair<Int, Int>, pc: Pair<Int, Int>, se: Pair<Int, Int>, sc: Pair<Int, Int>,
        te: Pair<Int, Int>, tc: Pair<Int, Int>, selection: Int, fab: Pair<Int, Int>, solid: Pair<Int, Int>,
        bar: Pair<Int, Int>, barField: Int, inversePrimary: Int,
    ): Map<String, Int> = linkedMapOf(
        "primary" to pr.first, "onPrimary" to pr.second, "primaryContainer" to pc.first, "onPrimaryContainer" to pc.second,
        "secondary" to se.first, "onSecondary" to se.second, "secondaryContainer" to sc.first, "onSecondaryContainer" to sc.second,
        "tertiary" to te.first, "onTertiary" to te.second, "tertiaryContainer" to tc.first, "onTertiaryContainer" to tc.second,
        "navActive" to sc.first, "navActiveFg" to sc.second, "focusRing" to pr.first, "link" to pr.first,
        "chip" to sc.first, "onChip" to sc.second, "badge" to tc.first, "onBadge" to tc.second,
        "selection" to selection, "fab" to fab.first, "onFab" to fab.second, "solid" to solid.first, "onSolid" to solid.second,
        "bar" to bar.first, "onBar" to bar.second, "barField" to barField, "inversePrimary" to inversePrimary,
    )

    private fun buildLight(p: HueChroma, s: HueChroma, t: HueChroma, t0: Int, seed: Int, seedTone: Double, ctx: Context): Map<String, Int> {
        val okPrimary = { c: Int -> contrast(c, WHITE) >= AA_TEXT && onSurfaces(c, ctx) }
        val seedKept = jsRound(seedTone) == t0 && okPrimary(seed)
        val pt = if (seedKept) t0 else push(p, t0, -1, okPrimary)
        val primary = fill(p, pt, tone(p, 10), okPrimary, if (seedKept) seed else tone(p, pt))
        val sel = HueChroma(p.hue, min(p.chroma, SELECTION_CHROMA))
        return roles(
            primary, pair(soft(p), 90, p, 10, -1),
            fill(s, 40, tone(s, 10)), pair(soft(s), 90, s, 10, -1),
            fill(t, 40, tone(t, 10)), pair(soft(t), 90, t, 10, -1),
            tone(sel, push(sel, 88, 1) { contrast(it, ctx.text) >= AA_TEXT }), fill(soft(p), 90, tone(p, 10)),
            primary, primary, WHITE, tone(p, push(p, 80, 1) { contrast(it, ctx.toast) >= AA_TEXT }),
        )
    }

    private fun buildDark(p: HueChroma, s: HueChroma, t: HueChroma, t0: Int, lightPrimary: Pair<Int, Int>, ctx: Context): Map<String, Int> {
        val d0 = max(60, min(92, 120 - t0))
        val okPrimary = { c: Int -> onSurfaces(c, ctx) }
        val dt = push(p, d0, 1, okPrimary)
        val sel = HueChroma(p.hue, min(p.chroma, SELECTION_CHROMA))
        return roles(
            fill(p, dt, tone(p, 20), okPrimary), pair(soft(p), 30, p, 90, 1),
            fill(s, 80, tone(s, 20)), pair(soft(s), 30, s, 90, 1),
            fill(t, 80, tone(t, 20)), pair(soft(t), 30, t, 90, 1),
            tone(sel, push(sel, 28, -1) { contrast(it, ctx.text) >= AA_TEXT }), fill(soft(p), 90, tone(p, 10)),
            lightPrimary, fill(p, 20, tone(p, 10)),
            tone(p, push(p, 28, -1) { contrast(it, ctx.text) >= AA_TEXT }),
            tone(p, push(p, 40, -1) { contrast(it, ctx.toast) >= AA_TEXT }),
        )
    }

    /** The app's Material 3 scheme with the seed's accents over the app's own neutral surfaces ([base]). */
    fun colorScheme(seedHex: String, harmony: Harmony, dark: Boolean, base: ColorScheme = if (dark) Dark else Light): ColorScheme {
        val ctx = Context(listOf(base.surface, base.background, base.surfaceContainer, base.surfaceContainerHigh).map { it.argb() },
            base.onSurface.argb(), base.inverseSurface.argb())
        val r = if (dark) build(seedHex, harmony, NEUTRAL_LIGHT, ctx).dark else build(seedHex, harmony, ctx, NEUTRAL_DARK).light
        fun c(k: String) = Color(r.getValue(k))
        return base.copy(
            primary = c("primary"), onPrimary = c("onPrimary"), primaryContainer = c("primaryContainer"), onPrimaryContainer = c("onPrimaryContainer"),
            secondary = c("secondary"), onSecondary = c("onSecondary"), secondaryContainer = c("secondaryContainer"), onSecondaryContainer = c("onSecondaryContainer"),
            tertiary = c("tertiary"), onTertiary = c("onTertiary"), tertiaryContainer = c("tertiaryContainer"), onTertiaryContainer = c("onTertiaryContainer"),
            inversePrimary = c("inversePrimary"), surfaceTint = c("primary"),
        )
    }

    private fun Color.argb(): Int =
        (0xFF shl 24) or (Math.round(red * 255) shl 16) or (Math.round(green * 255) shl 8) or Math.round(blue * 255)
}
