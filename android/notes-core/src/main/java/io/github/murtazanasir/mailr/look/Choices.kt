package io.github.murtazanasir.mailr.look

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ProvideTextStyle
import androidx.compose.material3.RadioButtonColors
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.SegmentedButtonColors
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Stable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.graphics.addOutline
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.material3.RadioButton as M3RadioButton
import androidx.compose.material3.SegmentedButton as M3SegmentedButton
import androidx.compose.material3.SingleChoiceSegmentedButtonRow as M3SingleChoiceSegmentedButtonRow

/*
 * Choices: radio buttons and the segmented row.
 * Each keeps the name and parameters of its Material 3 component; in Material's look it is that
 * component.
 */

// ---------------------------------------------------------------- radio buttons

/**
 * A radio button. In the web's look, components.css .radio (the browser's radio in the primary
 * colour): an 18 dp ring, 2 dp, in the secondary text colour when off and the primary colour with a
 * dot when on. With [onClick] it is a 48 dp target with Role.RadioButton; without one the row around
 * it takes the tap (and the role), as in Material.
 */
@Composable
fun RadioButton(
    selected: Boolean,
    onClick: (() -> Unit)?,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    colors: RadioButtonColors = RadioButtonDefaults.colors(),
    interactionSource: MutableInteractionSource? = null,
) {
    if (!look.web) return M3RadioButton(selected = selected, onClick = onClick, modifier = modifier, enabled = enabled, colors = colors,
        interactionSource = interactionSource)
    val ring = if (selected) MaterialTheme.colorScheme.primary else look.palette.text2
    val tap = if (onClick != null) Modifier.minimumInteractiveComponentSize().selectable(selected, interactionSource = interactionSource,
        indication = ripple(bounded = false, radius = 20.dp), enabled = enabled, role = Role.RadioButton, onClick = onClick) else Modifier
    Box(modifier.then(tap).alpha(if (enabled) 1f else 0.4f), contentAlignment = Alignment.Center) {
        // 24 dp like Material's (20 dp and its 2 dp padding), so rows keep their measure.
        Canvas(Modifier.size(24.dp).padding(3.dp)) {
            val stroke = 2.dp.toPx()
            drawCircle(ring, radius = (size.minDimension - stroke) / 2, style = Stroke(stroke))
            if (selected) drawCircle(ring, radius = 4.5.dp.toPx())
        }
    }
}

// ---------------------------------------------------------------- segmented buttons

/**
 * The scope of [SingleChoiceSegmentedButtonRow]: a row; in Material's look also Material's own scope,
 * which its [SegmentedButton] needs.
 */
@Stable
class SingleChoiceSegmentedButtonRowScope internal constructor(
    row: RowScope,
    internal val material: androidx.compose.material3.SingleChoiceSegmentedButtonRowScope?,
) : RowScope by row

private val SegmentInset = 6.dp
private val SegmentPill = RoundedCornerShape(50)

/**
 * A row of mutually exclusive choices. In the web's look, whenSheet.css .ws-seg: one 36 dp pill with a
 * 1 px border, its parts split by 1 px lines, the chosen one on the primary container. The row is 48 dp
 * tall, so each part is a full touch target; the pill sits in its middle. A selectable group, its
 * parts radio buttons for TalkBack, as Material's.
 */
@Composable
fun SingleChoiceSegmentedButtonRow(
    modifier: Modifier = Modifier,
    space: Dp = SegmentedButtonDefaults.BorderWidth,
    content: @Composable SingleChoiceSegmentedButtonRowScope.() -> Unit,
) {
    if (!look.web) return M3SingleChoiceSegmentedButtonRow(modifier, space) { SingleChoiceSegmentedButtonRowScope(this, this).content() }
    val line = look.palette.border
    Row(
        modifier
            .height(IntrinsicSize.Min)
            .heightIn(min = 48.dp)
            .selectableGroup()
            .drawWithContent {
                val inset = SegmentInset.toPx()
                val box = Size(size.width, size.height - 2 * inset)
                val outline = SegmentPill.createOutline(box, layoutDirection, this)
                translate(top = inset) {
                    clipPath(androidx.compose.ui.graphics.Path().apply { addOutline(outline) }) {
                        translate(top = -inset) { this@drawWithContent.drawContent() }
                    }
                    val w = 1.dp.toPx()
                    // The border inside the pill's edge, as CSS draws it.
                    val inner = SegmentPill.createOutline(Size(box.width - w, box.height - w), layoutDirection, this)
                    translate(w / 2, w / 2) {
                        drawOutline(inner, line, style = Stroke(w))
                    }
                }
            },
        verticalAlignment = Alignment.CenterVertically,
    ) { SingleChoiceSegmentedButtonRowScope(this, null).content() }
}

private fun androidx.compose.ui.graphics.drawscope.DrawScope.drawOutline(
    outline: androidx.compose.ui.graphics.Outline, color: androidx.compose.ui.graphics.Color, style: Stroke,
) = drawPath(androidx.compose.ui.graphics.Path().apply { addOutline(outline) }, color, style = style)

/**
 * One part of a [SingleChoiceSegmentedButtonRow]. In the web's look the row draws the pill, so [shape]
 * (Material's per-part corners) is not used; the label is 13 px medium in the secondary text colour, the
 * chosen part on the primary container. No tick: the web marks the choice by its fill only. [border]
 * null is Material's default border.
 */
@Composable
fun SingleChoiceSegmentedButtonRowScope.SegmentedButton(
    selected: Boolean,
    onClick: () -> Unit,
    shape: Shape,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    colors: SegmentedButtonColors = SegmentedButtonDefaults.colors(),
    border: BorderStroke? = null,
    interactionSource: MutableInteractionSource? = null,
    icon: @Composable () -> Unit = { if (!look.web) SegmentedButtonDefaults.Icon(selected) },
    label: @Composable () -> Unit,
) {
    val m3 = material
    if (m3 != null) {
        with(m3) {
            if (border == null) M3SegmentedButton(selected = selected, onClick = onClick, shape = shape, modifier = modifier, enabled = enabled,
                colors = colors, interactionSource = interactionSource, icon = icon, label = label)
            else M3SegmentedButton(selected = selected, onClick = onClick, shape = shape, modifier = modifier, enabled = enabled,
                colors = colors, border = border, interactionSource = interactionSource, icon = icon, label = label)
        }
        return
    }
    val cs = MaterialTheme.colorScheme
    val p = look.palette
    val line = p.border
    Box(
        modifier
            .weight(1f)
            .fillMaxHeight()
            .selectable(selected, interactionSource = interactionSource, indication = ripple(), enabled = enabled, role = Role.RadioButton,
                onClick = onClick)
            .drawBehind {
                val inset = SegmentInset.toPx()
                if (selected) drawRect(cs.primaryContainer, Offset(0f, inset), Size(size.width, size.height - 2 * inset))
                // The line on the start side; the first part's falls on the pill's own border.
                val w = 1.dp.toPx()
                val x = if (layoutDirection == LayoutDirection.Rtl) size.width - w / 2 else w / 2
                drawLine(line, Offset(x, inset), Offset(x, size.height - inset), w)
            }
            .padding(horizontal = 6.dp, vertical = SegmentInset)
            .alpha(if (enabled) 1f else 0.5f),
        contentAlignment = Alignment.Center,
    ) {
        CompositionLocalProvider(LocalContentColor provides if (selected) cs.onPrimaryContainer else p.text2) {
            ProvideTextStyle(WebTheme.style(WebTokens.ButtonSmall, LocalTextStyle.current).copy(textAlign = TextAlign.Center)) {
                Row(Modifier.heightIn(min = 36.dp), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp, Alignment.CenterHorizontally)) {
                    icon()
                    label()
                }
            }
        }
    }
}
