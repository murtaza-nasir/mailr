package io.github.murtazanasir.mailr.look

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.progressSemantics
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ProgressIndicatorDefaults
import androidx.compose.material3.ProvideTextStyle
import androidx.compose.material3.SnackbarData
import androidx.compose.material3.SnackbarDefaults
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.notes.R
import androidx.compose.material3.CircularProgressIndicator as M3CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator as M3LinearProgressIndicator
import androidx.compose.material3.Snackbar as M3Snackbar
import androidx.compose.material3.SnackbarHost as M3SnackbarHost

/*
 * Feedback: progress indicators, snackbars (the
 * web's toasts) and the banner tints. The stand-ins keep the names and parameters of Material 3's; in
 * Material's look each one is Material's.
 */

// ---------------------------------------------------------------- progress

/**
 * Spinning. In the web's look, components.css .spinner: 24 dp, a 3 dp ring on the primary container
 * with a quarter of it in the primary colour, turning once every 0.8 s. A size or stroke the screen
 * gives still wins (the web's small spinner is 16 dp with 2 dp).
 */
@Composable
fun CircularProgressIndicator(
    modifier: Modifier = Modifier,
    color: Color = if (look.web) MaterialTheme.colorScheme.primary else ProgressIndicatorDefaults.circularColor,
    strokeWidth: Dp = if (look.web) 3.dp else ProgressIndicatorDefaults.CircularStrokeWidth,
    trackColor: Color = if (look.web) spinnerTrack(color) else ProgressIndicatorDefaults.circularIndeterminateTrackColor,
    strokeCap: StrokeCap = ProgressIndicatorDefaults.CircularIndeterminateStrokeCap,
) {
    if (!look.web) return M3CircularProgressIndicator(modifier = modifier, color = color, strokeWidth = strokeWidth, trackColor = trackColor,
        strokeCap = strokeCap)
    val turn by rememberInfiniteTransition(label = "spinner").animateFloat(0f, 360f, // i18n-ignore: animation labels
        infiniteRepeatable(tween(800, easing = LinearEasing), RepeatMode.Restart), label = "turn") // i18n-ignore: animation label
    Canvas(modifier.progressSemantics().size(24.dp)) { spinner(trackColor, color, strokeWidth.toPx(), turn - 90f, 90f) }
}

/** Determinate: the same ring, filled in the primary colour from the top as [progress] grows. */
@Composable
fun CircularProgressIndicator(
    progress: () -> Float,
    modifier: Modifier = Modifier,
    color: Color = if (look.web) MaterialTheme.colorScheme.primary else ProgressIndicatorDefaults.circularColor,
    strokeWidth: Dp = if (look.web) 3.dp else ProgressIndicatorDefaults.CircularStrokeWidth,
    trackColor: Color = if (look.web) spinnerTrack(color) else ProgressIndicatorDefaults.circularDeterminateTrackColor,
    strokeCap: StrokeCap = ProgressIndicatorDefaults.CircularDeterminateStrokeCap,
    gapSize: Dp = ProgressIndicatorDefaults.CircularIndicatorTrackGapSize,
) {
    if (!look.web) return M3CircularProgressIndicator(progress = progress, modifier = modifier, color = color, strokeWidth = strokeWidth,
        trackColor = trackColor, strokeCap = strokeCap, gapSize = gapSize)
    val value = progress().coerceIn(0f, 1f)
    Canvas(modifier.progressSemantics(value).size(24.dp)) { spinner(trackColor, color, strokeWidth.toPx(), -90f, 360f * value) }
}

/** The ring under the arc: the primary container under the primary colour, else the arc's own colour, faint (a spinner on a button). */
@Composable
private fun spinnerTrack(arc: Color): Color = MaterialTheme.colorScheme.let { if (arc == it.primary) it.primaryContainer else arc.copy(alpha = 0.3f) }

private fun DrawScope.spinner(track: Color, arc: Color, stroke: Float, start: Float, sweep: Float) {
    val d = size.minDimension - stroke
    val topLeft = Offset((size.width - d) / 2, (size.height - d) / 2)
    drawArc(track, 0f, 360f, false, topLeft, Size(d, d), style = Stroke(stroke))
    drawArc(arc, start, sweep, false, topLeft, Size(d, d), style = Stroke(stroke))
}

/**
 * A progress bar. In the web's look, compose.css .compose-att .progress: 4 dp, rounded, the primary
 * colour on the divider colour; while nothing is known a third of it slides across. A height or track
 * colour the screen gives still wins.
 */
@Composable
fun LinearProgressIndicator(
    modifier: Modifier = Modifier,
    color: Color = if (look.web) MaterialTheme.colorScheme.primary else ProgressIndicatorDefaults.linearColor,
    trackColor: Color = if (look.web) look.palette.divider else ProgressIndicatorDefaults.linearTrackColor,
    strokeCap: StrokeCap = ProgressIndicatorDefaults.LinearStrokeCap,
    gapSize: Dp = ProgressIndicatorDefaults.LinearIndicatorTrackGapSize,
) {
    if (!look.web) return M3LinearProgressIndicator(modifier = modifier, color = color, trackColor = trackColor, strokeCap = strokeCap,
        gapSize = gapSize)
    val at by rememberInfiniteTransition(label = "bar").animateFloat(-0.35f, 1f, // i18n-ignore: animation label
        infiniteRepeatable(tween(1200, easing = LinearEasing), RepeatMode.Restart), label = "at") // i18n-ignore: animation label
    Canvas(modifier.progressSemantics().size(240.dp, 4.dp)) { bar(trackColor, color, at, at + 0.35f) }
}

@Composable
fun LinearProgressIndicator(
    progress: () -> Float,
    modifier: Modifier = Modifier,
    color: Color = if (look.web) MaterialTheme.colorScheme.primary else ProgressIndicatorDefaults.linearColor,
    trackColor: Color = if (look.web) look.palette.divider else ProgressIndicatorDefaults.linearTrackColor,
    strokeCap: StrokeCap = ProgressIndicatorDefaults.LinearStrokeCap,
    gapSize: Dp = ProgressIndicatorDefaults.LinearIndicatorTrackGapSize,
    drawStopIndicator: DrawScope.() -> Unit = {
        ProgressIndicatorDefaults.drawStopIndicator(this, stopSize = ProgressIndicatorDefaults.LinearTrackStopIndicatorSize, color = color,
            strokeCap = strokeCap)
    },
) {
    if (!look.web) return M3LinearProgressIndicator(progress = progress, modifier = modifier, color = color, trackColor = trackColor,
        strokeCap = strokeCap, gapSize = gapSize, drawStopIndicator = drawStopIndicator)
    val value = progress().coerceIn(0f, 1f)
    Canvas(modifier.progressSemantics(value).size(240.dp, 4.dp)) { bar(trackColor, color, 0f, value) }
}

/** The track, then the part from [from] to [to] (fractions of the width, from the start side). */
private fun DrawScope.bar(track: Color, fill: Color, from: Float, to: Float) {
    val r = CornerRadius(size.height / 2)
    if (track.alpha > 0f) drawRoundRect(track, cornerRadius = r)
    val a = from.coerceIn(0f, 1f) * size.width
    val b = to.coerceIn(0f, 1f) * size.width
    if (b <= a) return
    val left = if (layoutDirection == LayoutDirection.Rtl) size.width - b else a
    drawRoundRect(fill, Offset(left, 0f), Size(b - a, size.height), r)
}

// ---------------------------------------------------------------- snackbars

/**
 * Where snackbars appear: Material's host (its timing, its TalkBack announcement and dismiss action),
 * each one drawn by [Snackbar].
 */
@Composable
fun SnackbarHost(
    hostState: SnackbarHostState,
    modifier: Modifier = Modifier,
    snackbar: @Composable (SnackbarData) -> Unit = { Snackbar(it) },
) = M3SnackbarHost(hostState, modifier, snackbar)

/**
 * A snackbar. In the web's look, components.css .toast on a phone (phone.css): the full width less
 * 12 dp each side, at least 48 dp tall, 4 dp corners, the toast colours, a 14 px message, the action
 * in the toast's action colour (medium) and a close button when the snackbar has one.
 */
@Composable
fun Snackbar(
    snackbarData: SnackbarData,
    modifier: Modifier = Modifier,
    actionOnNewLine: Boolean = false,
    shape: Shape = if (look.web) RoundedCornerShape(WebTokens.RadiusXs) else SnackbarDefaults.shape,
    containerColor: Color = if (look.web) look.palette.toastBg else SnackbarDefaults.color,
    contentColor: Color = if (look.web) look.palette.toastFg else SnackbarDefaults.contentColor,
    actionColor: Color = if (look.web) look.palette.toastAction else SnackbarDefaults.actionColor,
    actionContentColor: Color = if (look.web) look.palette.toastAction else SnackbarDefaults.actionContentColor,
    dismissActionContentColor: Color = if (look.web) look.palette.toastFg else SnackbarDefaults.dismissActionContentColor,
) {
    if (!look.web) return M3Snackbar(snackbarData, modifier, actionOnNewLine, shape, containerColor, contentColor, actionColor,
        actionContentColor, dismissActionContentColor)
    val v = snackbarData.visuals
    Surface(modifier.fillMaxWidth().padding(12.dp), shape = shape, color = containerColor, contentColor = contentColor, shadowElevation = 6.dp) {
        Row(Modifier.heightIn(min = 48.dp).padding(start = 16.dp, end = 8.dp, top = 6.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ProvideTextStyle(WebTheme.style(WebTokens.Toast, LocalTextStyle.current)) {
                Box(Modifier.weight(1f).padding(vertical = 6.dp)) { Text(v.message) }
                v.actionLabel?.let { label ->
                    TextButton(onClick = { snackbarData.performAction() }, contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 8.dp),
                        colors = androidx.compose.material3.ButtonColors(Color.Transparent, actionColor, Color.Transparent, actionColor)) {
                        Text(label, fontWeight = FontWeight.Medium)
                    }
                }
            }
            if (v.withDismissAction) CompositionLocalProvider(LocalContentColor provides dismissActionContentColor) {
                IconButton(onClick = { snackbarData.dismiss() }) {
                    Icon(Icons.Outlined.Close, stringResource(R.string.look_dismiss), Modifier.size(18.dp))
                }
            }
        }
    }
}

// ---------------------------------------------------------------- banners

/** What a banner says, for its tint: components.css .banner (info), .banner.warn, .banner.error; the Notes app's plain .nt-banner. */
enum class BannerTone { Neutral, Info, Warning, Danger }

@Immutable
data class BannerColors(val container: Color, val content: Color)

/**
 * A banner's colours. In the web's look the tone's tint from the web's tokens (info container with the
 * text colour, warning container with the warning text, danger container with its own text, the dim
 * surface with the secondary text); in Material's look the colours the screen chose.
 */
@Composable
fun bannerColors(tone: BannerTone, materialContainer: Color, materialContent: Color): BannerColors {
    if (!look.web) return BannerColors(materialContainer, materialContent)
    val p = look.palette
    return when (tone) {
        BannerTone.Neutral -> BannerColors(p.surfaceDim, p.text2)
        BannerTone.Info -> BannerColors(p.infoContainer, p.text)
        BannerTone.Warning -> BannerColors(p.warningContainer, p.warningFg)
        BannerTone.Danger -> BannerColors(p.dangerContainer, p.onDangerContainer)
    }
}
