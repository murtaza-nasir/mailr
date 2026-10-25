package io.github.murtazanasir.mailr.i18n

import androidx.compose.material3.Typography
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDirection

/**
 * Every text style takes its direction from its own text, as an
 * Android TextView does by default (firstStrong). Compose would otherwise lay text out in the
 * screen's direction, so an English subject in an Arabic screen showed its question mark at the
 * start, and an Arabic name in an English screen its punctuation at the wrong end. Text with no
 * letters (a time, a count) still follows the screen.
 *
 * The text still lines up with the screen's start side ([rtl]: the right), as a TextView's gravity
 * does: an English subject in an Arabic list sits on the right, next to the sender. A Text that
 * sets its own textAlign keeps it.
 */
fun Typography.contentDirection(rtl: Boolean = false): Typography = copy(
    displayLarge = displayLarge.copy(textDirection = TextDirection.Content, textAlign = side(rtl)),
    displayMedium = displayMedium.copy(textDirection = TextDirection.Content, textAlign = side(rtl)),
    displaySmall = displaySmall.copy(textDirection = TextDirection.Content, textAlign = side(rtl)),
    headlineLarge = headlineLarge.copy(textDirection = TextDirection.Content, textAlign = side(rtl)),
    headlineMedium = headlineMedium.copy(textDirection = TextDirection.Content, textAlign = side(rtl)),
    headlineSmall = headlineSmall.copy(textDirection = TextDirection.Content, textAlign = side(rtl)),
    titleLarge = titleLarge.copy(textDirection = TextDirection.Content, textAlign = side(rtl)),
    titleMedium = titleMedium.copy(textDirection = TextDirection.Content, textAlign = side(rtl)),
    titleSmall = titleSmall.copy(textDirection = TextDirection.Content, textAlign = side(rtl)),
    bodyLarge = bodyLarge.copy(textDirection = TextDirection.Content, textAlign = side(rtl)),
    bodyMedium = bodyMedium.copy(textDirection = TextDirection.Content, textAlign = side(rtl)),
    bodySmall = bodySmall.copy(textDirection = TextDirection.Content, textAlign = side(rtl)),
    labelLarge = labelLarge.copy(textDirection = TextDirection.Content, textAlign = side(rtl)),
    labelMedium = labelMedium.copy(textDirection = TextDirection.Content, textAlign = side(rtl)),
    labelSmall = labelSmall.copy(textDirection = TextDirection.Content, textAlign = side(rtl)),
)

private fun side(rtl: Boolean) = if (rtl) TextAlign.Right else TextAlign.Left
