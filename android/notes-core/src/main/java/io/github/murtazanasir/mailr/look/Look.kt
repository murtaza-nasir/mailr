package io.github.murtazanasir.mailr.look

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight

/**
 * The design the app wears: the web app's Classic look,
 * the apps' default, or Material's (mailr's Settings → Look → Style). The host's theme provides it; the
 * components in this package and the few screen details that differ read it. With [web] false every one
 * of them is Material's own, as without a theme.
 */
@Immutable
data class Look(val web: Boolean = false, val dark: Boolean = false) {
    /** The web's neutrals for this scheme (accents may follow a colour of the user's own: read those from the colour scheme). */
    val palette: WebPalette get() = if (dark) WebTokens.Dark else WebTokens.Light

    companion object {
        val Material = Look()
    }
}

val LocalLook = staticCompositionLocalOf { Look.Material }

/** The current look. */
val look: Look @Composable @ReadOnlyComposable get() = LocalLook.current

/** The web's Classic theme as Material 3 roles, built from [WebTokens] only. */
object WebTheme {
    /** Classic's own colour (the web's default preset, classic.ocean): the tokens as they are. */
    const val CLASSIC_SEED = "#1f6c86"

    fun colorScheme(dark: Boolean): ColorScheme {
        val p = if (dark) WebTokens.Dark else WebTokens.Light
        return (if (dark) darkColorScheme() else lightColorScheme()).copy(
            primary = p.primary, onPrimary = p.onPrimary,
            primaryContainer = p.primaryContainer, onPrimaryContainer = p.onPrimaryContainer,
            inversePrimary = p.toastAction,
            secondary = p.secondary, onSecondary = p.onPrimary,
            secondaryContainer = p.navActive, onSecondaryContainer = p.navActiveFg,
            tertiary = p.tertiary, onTertiary = p.onPrimary,
            tertiaryContainer = p.tertiaryContainer, onTertiaryContainer = p.onTertiaryContainer,
            background = p.bg, onBackground = p.text,
            surface = p.surface, onSurface = p.text,
            surfaceVariant = p.inputBg, onSurfaceVariant = p.text2,
            // Flat: no tonal elevation tint anywhere.
            surfaceTint = Color.Transparent,
            inverseSurface = p.toastBg, inverseOnSurface = p.toastFg,
            error = p.danger, onError = if (dark) p.dangerContainer else Color.White,
            errorContainer = p.dangerContainer, onErrorContainer = p.onDangerContainer,
            outline = p.border, outlineVariant = p.divider,
            scrim = Color(0xFF202124),
            surfaceBright = p.surface,
            surfaceContainer = p.surfaceContainer,
            surfaceContainerHigh = p.surfaceHigh,
            surfaceContainerHighest = p.chipBg,
            surfaceContainerLow = p.surfaceDim,
            surfaceContainerLowest = p.surface,
            surfaceDim = p.surfaceDim,
        )
    }

    fun style(t: WebType, base: TextStyle = TextStyle.Default): TextStyle = base.copy(
        fontSize = t.size, lineHeight = t.lineHeight, fontWeight = FontWeight(t.weight), letterSpacing = t.letterSpacing,
    )

    /**
     * Material's roles at the web's sizes. The font stays the system's: the PWA asks for Inter, then
     * Noto Sans, then Roboto, and an Android phone has only the last.
     */
    fun typography(m: Typography = Typography()): Typography = m.copy(
        headlineSmall = style(WebTokens.DialogTitle, m.headlineSmall),
        titleLarge = style(WebTokens.DialogTitle, m.titleLarge),
        titleMedium = style(WebTokens.SheetTitle, m.titleMedium).copy(fontSize = WebTokens.MoreRow.size, lineHeight = WebTokens.MoreRow.lineHeight),
        titleSmall = style(WebTokens.Row, m.titleSmall).copy(fontWeight = FontWeight.Medium),
        bodyLarge = style(WebTokens.MoreRow, m.bodyLarge),
        bodyMedium = style(WebTokens.Row, m.bodyMedium),
        bodySmall = style(WebTokens.RowDate, m.bodySmall),
        labelLarge = style(WebTokens.Button, m.labelLarge),
        labelMedium = style(WebTokens.Pill, m.labelMedium),
        labelSmall = style(WebTokens.Badge, m.labelSmall).copy(fontWeight = FontWeight.Medium),
    )

    /** Fields and menus 8, chips 8, cards 12, the Compose button 16, dialogs and sheets 24 (tokens.css radii). */
    val shapes = Shapes(
        extraSmall = RoundedCornerShape(WebTokens.RadiusSm),
        small = RoundedCornerShape(WebTokens.RadiusSm),
        medium = RoundedCornerShape(WebTokens.RadiusMd),
        large = RoundedCornerShape(WebTokens.RadiusLg),
        extraLarge = RoundedCornerShape(WebTokens.RadiusXl),
    )
}
