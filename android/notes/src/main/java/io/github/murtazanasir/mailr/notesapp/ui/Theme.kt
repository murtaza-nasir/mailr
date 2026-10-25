package io.github.murtazanasir.mailr.notesapp.ui

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import io.github.murtazanasir.mailr.notesapp.data.NotesPrefs
import io.github.murtazanasir.mailr.notesapp.data.ThemeChoice
import io.github.murtazanasir.mailr.i18n.contentDirection
import io.github.murtazanasir.mailr.look.LocalLook
import io.github.murtazanasir.mailr.look.Look
import io.github.murtazanasir.mailr.look.WebTheme

// The Notes app's own palette: a warm amber, as a paper note; Material 3 tones, light and dark.
private val Light = lightColorScheme(
    primary = Color(0xFF7C5800), onPrimary = Color.White, primaryContainer = Color(0xFFFFDEA6), onPrimaryContainer = Color(0xFF271900),
    secondary = Color(0xFF6C5C3F), onSecondary = Color.White, secondaryContainer = Color(0xFFF6DFBB), onSecondaryContainer = Color(0xFF251A04),
    tertiary = Color(0xFF4B6546), tertiaryContainer = Color(0xFFCDEBC4), onTertiaryContainer = Color(0xFF082008),
    background = Color(0xFFFFFBF5), onBackground = Color(0xFF1F1B13), surface = Color(0xFFFFFBF5), onSurface = Color(0xFF1F1B13),
    surfaceVariant = Color(0xFFEEE0CF), onSurfaceVariant = Color(0xFF4E4639),
    surfaceContainerLowest = Color.White, surfaceContainerLow = Color(0xFFFBF3E8), surfaceContainer = Color(0xFFF5EDE2),
    surfaceContainerHigh = Color(0xFFEFE7DC), surfaceContainerHighest = Color(0xFFE9E1D7),
    outline = Color(0xFF807667), outlineVariant = Color(0xFFD1C5B4),
    error = Color(0xFFBA1A1A), errorContainer = Color(0xFFFFDAD6),
)

private val Dark = darkColorScheme(
    primary = Color(0xFFF8BD3B), onPrimary = Color(0xFF412D00), primaryContainer = Color(0xFF5E4200), onPrimaryContainer = Color(0xFFFFDEA6),
    secondary = Color(0xFFD9C4A0), onSecondary = Color(0xFF3B2F15), secondaryContainer = Color(0xFF53452A), onSecondaryContainer = Color(0xFFF6DFBB),
    tertiary = Color(0xFFB1CFA9), tertiaryContainer = Color(0xFF344D30), onTertiaryContainer = Color(0xFFCDEBC4),
    background = Color(0xFF17130B), onBackground = Color(0xFFEBE1D4), surface = Color(0xFF17130B), onSurface = Color(0xFFEBE1D4),
    surfaceVariant = Color(0xFF4E4639), onSurfaceVariant = Color(0xFFD1C5B4),
    surfaceContainerLowest = Color(0xFF110E07), surfaceContainerLow = Color(0xFF1F1B13), surfaceContainer = Color(0xFF231F17),
    surfaceContainerHigh = Color(0xFF2E2921), surfaceContainerHighest = Color(0xFF39342B),
    outline = Color(0xFF9A8F80), outlineVariant = Color(0xFF4E4639),
    error = Color(0xFFFFB4AB), errorContainer = Color(0xFF93000A),
)

/**
 * The web app's look (io.github.murtazanasir.mailr.look, as the mailr app's default): the web's neutrals, type,
 * shapes and components, with the Notes app's amber as its accent; light or dark (as the phone, or
 * chosen), with the phone's own colours when asked (Android 12+).
 */
@Composable
fun NotesTheme(look: NotesPrefs.Look, content: @Composable () -> Unit) {
    val dark = when (look.theme) {
        ThemeChoice.SYSTEM -> isSystemInDarkTheme()
        ThemeChoice.LIGHT -> false
        ThemeChoice.DARK -> true
    }
    val ctx = LocalContext.current
    val scheme = when {
        look.dynamicColour && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> if (dark) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)
        else -> androidx.compose.runtime.remember(dark) { amberOverWeb(dark) }
    }
    // Text takes its own direction, lined up with the screen's start side (i18n/TextDirections.kt).
    val rtl = androidx.compose.ui.platform.LocalLayoutDirection.current == androidx.compose.ui.unit.LayoutDirection.Rtl
    val typography = androidx.compose.runtime.remember(rtl) { WebTheme.typography().contentDirection(rtl) }
    androidx.compose.runtime.CompositionLocalProvider(LocalLook provides Look(web = true, dark = dark)) {
        MaterialTheme(colorScheme = scheme, typography = typography, shapes = WebTheme.shapes, content = content)
    }
}

/** The web's Classic surfaces and text with the Notes app's amber accent roles. */
private fun amberOverWeb(dark: Boolean) = WebTheme.colorScheme(dark).let { web ->
    val a = if (dark) Dark else Light
    web.copy(
        primary = a.primary, onPrimary = a.onPrimary, primaryContainer = a.primaryContainer, onPrimaryContainer = a.onPrimaryContainer,
        inversePrimary = a.inversePrimary,
        secondary = a.secondary, onSecondary = a.onSecondary, secondaryContainer = a.secondaryContainer, onSecondaryContainer = a.onSecondaryContainer,
        tertiary = a.tertiary, tertiaryContainer = a.tertiaryContainer, onTertiaryContainer = a.onTertiaryContainer,
    )
}
