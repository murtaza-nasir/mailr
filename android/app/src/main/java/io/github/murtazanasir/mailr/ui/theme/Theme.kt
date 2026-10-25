package io.github.murtazanasir.mailr.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.i18n.contentDirection

// mailr brand: a clear, calm blue with warm accents for importance and stars.
private val Blue10 = Color(0xFF001B3F)
private val Blue20 = Color(0xFF002F65)
private val Blue30 = Color(0xFF00458F)
private val Blue40 = Color(0xFF1A5DC2)
private val Blue80 = Color(0xFFAAC7FF)
private val Blue90 = Color(0xFFD6E3FF)
private val Slate10 = Color(0xFF191C20)
private val Slate20 = Color(0xFF2E3036)
private val Slate90 = Color(0xFFE2E2E9)
private val Slate95 = Color(0xFFF0F0F7)
private val Slate99 = Color(0xFFFDFBFF)

internal val Light = lightColorScheme(
    primary = Blue40, onPrimary = Color.White, primaryContainer = Blue90, onPrimaryContainer = Blue10,
    secondary = Color(0xFF555F71), onSecondary = Color.White, secondaryContainer = Color(0xFFD9E3F8),
    onSecondaryContainer = Color(0xFF121C2B),
    tertiary = Color(0xFF6F5675), tertiaryContainer = Color(0xFFF9D8FE), onTertiaryContainer = Color(0xFF28132E),
    background = Slate99, onBackground = Slate10, surface = Slate99, onSurface = Slate10,
    surfaceVariant = Color(0xFFE0E2EC), onSurfaceVariant = Color(0xFF44474E),
    surfaceContainerLowest = Color.White, surfaceContainerLow = Color(0xFFF6F6FD),
    surfaceContainer = Color(0xFFF0F0F7), surfaceContainerHigh = Color(0xFFEAEAF1),
    surfaceContainerHighest = Color(0xFFE4E4EB),
    outline = Color(0xFF74777F), outlineVariant = Color(0xFFC4C6D0),
    error = Color(0xFFBA1A1A), errorContainer = Color(0xFFFFDAD6),
)

internal val Dark = darkColorScheme(
    primary = Blue80, onPrimary = Blue20, primaryContainer = Blue30, onPrimaryContainer = Blue90,
    secondary = Color(0xFFBDC7DC), onSecondary = Color(0xFF273141), secondaryContainer = Color(0xFF3E4758),
    onSecondaryContainer = Color(0xFFD9E3F8),
    tertiary = Color(0xFFDCBCE1), tertiaryContainer = Color(0xFF563E5C), onTertiaryContainer = Color(0xFFF9D8FE),
    background = Color(0xFF111318), onBackground = Slate90, surface = Color(0xFF111318), onSurface = Slate90,
    surfaceVariant = Color(0xFF44474E), onSurfaceVariant = Color(0xFFC4C6D0),
    surfaceContainerLowest = Color(0xFF0C0E13), surfaceContainerLow = Color(0xFF191C20),
    surfaceContainer = Color(0xFF1D2024), surfaceContainerHigh = Color(0xFF282A2F),
    surfaceContainerHighest = Slate20,
    outline = Color(0xFF8E9099), outlineVariant = Color(0xFF44474E),
    error = Color(0xFFFFB4AB), errorContainer = Color(0xFF93000A),
)

/** Colors outside the M3 scheme that mail needs (stars, importance, unread emphasis). */
@Immutable
data class MailColors(
    val star: Color,
    val important: Color,
    val importantBorder: Color,
    val unreadDot: Color,
    val swipeArchive: Color,
    val swipeDelete: Color,
    val swipeSnooze: Color,
    val swipeRead: Color,
    val success: Color,
    val warning: Color,
)

private val LightMail = MailColors(
    // The web Classic theme's tokens (frontend/src/styles/tokens.css): an amber star and a coral
    // importance tag.
    star = Color(0xFFE3A21A), important = Color(0xFFE98A5B), importantBorder = Color(0xFFC96A3C), unreadDot = Blue40,
    swipeArchive = Color(0xFF1E8E3E), swipeDelete = Color(0xFFD93025), swipeSnooze = Color(0xFFE37400),
    swipeRead = Blue40, success = Color(0xFF1E8E3E), warning = Color(0xFFB06000),
)
private val DarkMail = MailColors(
    star = Color(0xFFF2C45A), important = Color(0xFFF4A27A), importantBorder = Color(0xFFE98A5B), unreadDot = Blue80,
    swipeArchive = Color(0xFF5BB974), swipeDelete = Color(0xFFF28B82), swipeSnooze = Color(0xFFFCAD70),
    swipeRead = Blue80, success = Color(0xFF81C995), warning = Color(0xFFFDD663),
)

val LocalMailColors = staticCompositionLocalOf { LightMail }

private val AppTypography = Typography().let { t ->
    t.copy(
        titleLarge = t.titleLarge.copy(fontWeight = FontWeight.Normal, letterSpacing = 0.sp),
        titleMedium = t.titleMedium.copy(fontWeight = FontWeight.Medium),
        bodyLarge = t.bodyLarge.copy(lineHeight = 24.sp),
        labelLarge = t.labelLarge.copy(fontWeight = FontWeight.Medium),
    )
}

/** Unread rows are set in a heavier weight, like Gmail. */
val TextStyle.unread: TextStyle get() = copy(fontWeight = FontWeight.Bold)

private val AppShapes = Shapes(
    extraSmall = RoundedCornerShape(6.dp),
    small = RoundedCornerShape(10.dp),
    medium = RoundedCornerShape(16.dp),
    large = RoundedCornerShape(24.dp),
    extraLarge = RoundedCornerShape(32.dp),
)

/** The web's colour choice as the phone keeps it (Settings → Look → Colours: "Same as mailr on the web"). */
data class WebColours(val seed: String, val harmony: SeedScheme.Harmony)

/**
 * [dynamic]: the wallpaper's colours (Android 12+). [web]: the colours of mailr on the web, built from
 * its seed (SeedScheme). Neither: mailr's own palette.
 *
 * [webLook]: the web app's Classic look (io.github.murtazanasir.mailr.look), the default (Settings → Look → Style,
 * [io.github.murtazanasir.mailr.data.settings.Prefs.webLook]); off, Android's Material. Its neutrals, type and
 * shapes are the web's tokens; mailr's own palette is Classic's, and a colour of the user's own on
 * the web sets the accents over Classic's surfaces.
 */
@Composable
fun MailrTheme(mode: ThemeMode = ThemeMode.SYSTEM, dynamic: Boolean = true, web: WebColours? = null,
               webLook: Boolean = true, content: @Composable () -> Unit) {
    if (webLook) return WebLookTheme(mode, dynamic, web, content)
    val dark = when (mode) {
        ThemeMode.SYSTEM -> isSystemInDarkTheme()
        ThemeMode.LIGHT -> false
        ThemeMode.DARK -> true
    }
    val ctx = LocalContext.current
    val webScheme = androidx.compose.runtime.remember(web, dark) { web?.let { SeedScheme.colorScheme(it.seed, it.harmony, dark) } }
    val scheme: ColorScheme = when {
        webScheme != null -> webScheme
        dynamic && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ->
            if (dark) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)
        dark -> Dark
        else -> Light
    }
    // Links in text other people wrote open only as web, mailto: or tel: links (ui/util/SafeLinks).
    val links = androidx.compose.runtime.remember(ctx) { io.github.murtazanasir.mailr.ui.util.SafeUriHandler(ctx) }
    val mail = (if (dark) DarkMail else LightMail).let { m -> if (webScheme != null) m.copy(unreadDot = webScheme.primary, swipeRead = webScheme.primary) else m }
    androidx.compose.runtime.CompositionLocalProvider(LocalMailColors provides mail,
        androidx.compose.ui.platform.LocalUriHandler provides links) {
        // Text takes its own direction, lined up with the screen's start side (i18n/TextDirections.kt).
        val rtl = androidx.compose.ui.platform.LocalLayoutDirection.current == androidx.compose.ui.unit.LayoutDirection.Rtl
        val typography = androidx.compose.runtime.remember(rtl) { AppTypography.contentDirection(rtl) }
        MaterialTheme(colorScheme = scheme, typography = typography, shapes = AppShapes, content = content)
    }
}

object Mail {
    val colors: MailColors @Composable get() = LocalMailColors.current
}

/** [MailrTheme] in the web app's look. */
@Composable
private fun WebLookTheme(mode: ThemeMode, dynamic: Boolean, web: WebColours?, content: @Composable () -> Unit) {
    val dark = when (mode) {
        ThemeMode.SYSTEM -> isSystemInDarkTheme()
        ThemeMode.LIGHT -> false
        ThemeMode.DARK -> true
    }
    val ctx = LocalContext.current
    val classic = androidx.compose.runtime.remember(dark) { io.github.murtazanasir.mailr.look.WebTheme.colorScheme(dark) }
    val own = web?.takeUnless { it.seed.equals(io.github.murtazanasir.mailr.look.WebTheme.CLASSIC_SEED, ignoreCase = true) && it.harmony == SeedScheme.Harmony.SINGLE }
    val seeded = androidx.compose.runtime.remember(own, dark) { own?.let { SeedScheme.colorScheme(it.seed, it.harmony, dark, classic) } }
    val scheme: ColorScheme = when {
        seeded != null -> seeded
        web == null && dynamic && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ->
            if (dark) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)
        else -> classic
    }
    val look = io.github.murtazanasir.mailr.look.Look(web = true, dark = dark)
    val p = look.palette
    val mail = MailColors(
        star = p.star, important = p.important, importantBorder = p.importantBorder, unreadDot = scheme.primary,
        swipeArchive = p.success, swipeDelete = p.danger, swipeSnooze = p.warning, swipeRead = scheme.primary,
        success = p.success, warning = p.warning,
    )
    val links = androidx.compose.runtime.remember(ctx) { io.github.murtazanasir.mailr.ui.util.SafeUriHandler(ctx) }
    androidx.compose.runtime.CompositionLocalProvider(LocalMailColors provides mail,
        io.github.murtazanasir.mailr.look.LocalLook provides look,
        androidx.compose.ui.platform.LocalUriHandler provides links) {
        val rtl = androidx.compose.ui.platform.LocalLayoutDirection.current == androidx.compose.ui.unit.LayoutDirection.Rtl
        val typography = androidx.compose.runtime.remember(rtl) { io.github.murtazanasir.mailr.look.WebTheme.typography().contentDirection(rtl) }
        MaterialTheme(colorScheme = scheme, typography = typography, shapes = io.github.murtazanasir.mailr.look.WebTheme.shapes, content = content)
    }
}
