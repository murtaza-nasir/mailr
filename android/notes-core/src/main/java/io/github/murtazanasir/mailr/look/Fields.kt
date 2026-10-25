package io.github.murtazanasir.mailr.look

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ProvideTextStyle
import androidx.compose.material3.Text
import androidx.compose.material3.TextFieldColors
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.material3.TextField as M3TextField

/*
 * Inline fields and the select field. [TextField]
 * keeps the name and parameters of Material 3's; in Material's look it is Material's.
 */

/**
 * An inline field: a title in an editor, a search bar, "Add subtask". In the web's look,
 * components.css .input.underline: no box, a 1 px line in the border colour that turns 2 dp primary
 * when focused (the danger colour on an error), the placeholder in the tertiary text colour. A line
 * the screen made transparent stays off, so a borderless field stays borderless; a filled field (the
 * screen's container not transparent) is the web's filled input (input-bg, 8 dp corners unless the
 * screen gave a shape) without a line. Material's 16 dp side padding stays, so the screens' alignment
 * holds, and the field is at least 48 dp tall. The text is the screen's style, or the web's 16 px
 * search text on one line and its 15 px body text on several.
 */
@Composable
fun TextField(
    value: String,
    onValueChange: (String) -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    readOnly: Boolean = false,
    textStyle: TextStyle = LocalTextStyle.current,
    label: @Composable (() -> Unit)? = null,
    placeholder: @Composable (() -> Unit)? = null,
    leadingIcon: @Composable (() -> Unit)? = null,
    trailingIcon: @Composable (() -> Unit)? = null,
    prefix: @Composable (() -> Unit)? = null,
    suffix: @Composable (() -> Unit)? = null,
    supportingText: @Composable (() -> Unit)? = null,
    isError: Boolean = false,
    visualTransformation: VisualTransformation = VisualTransformation.None,
    keyboardOptions: KeyboardOptions = KeyboardOptions.Default,
    keyboardActions: KeyboardActions = KeyboardActions.Default,
    singleLine: Boolean = false,
    maxLines: Int = if (singleLine) 1 else Int.MAX_VALUE,
    minLines: Int = 1,
    interactionSource: MutableInteractionSource? = null,
    shape: Shape = TextFieldDefaults.shape,
    colors: TextFieldColors = TextFieldDefaults.colors(),
) {
    if (!look.web) return M3TextField(value, onValueChange, modifier, enabled, readOnly, textStyle, label, placeholder, leadingIcon,
        trailingIcon, prefix, suffix, supportingText, isError, visualTransformation, keyboardOptions, keyboardActions, singleLine, maxLines,
        minLines, interactionSource, shape, colors)
    val source = interactionSource ?: remember { MutableInteractionSource() }
    val focused by source.collectIsFocusedAsState()
    val defaults = TextFieldDefaults.colors()
    BasicTextField(value, onValueChange, modifier, enabled, readOnly, inlineStyle(textStyle, singleLine), keyboardOptions, keyboardActions,
        singleLine, maxLines, minLines, visualTransformation, interactionSource = source,
        cursorBrush = SolidColor(if (isError) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary),
        decorationBox = { inner ->
            InlineDecoration(focused, isError, enabled, colors, defaults, shape, singleLine, value.isEmpty(), label, placeholder, leadingIcon,
                trailingIcon, prefix, suffix, supportingText, inner)
        })
}

@Composable
fun TextField(
    value: TextFieldValue,
    onValueChange: (TextFieldValue) -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    readOnly: Boolean = false,
    textStyle: TextStyle = LocalTextStyle.current,
    label: @Composable (() -> Unit)? = null,
    placeholder: @Composable (() -> Unit)? = null,
    leadingIcon: @Composable (() -> Unit)? = null,
    trailingIcon: @Composable (() -> Unit)? = null,
    prefix: @Composable (() -> Unit)? = null,
    suffix: @Composable (() -> Unit)? = null,
    supportingText: @Composable (() -> Unit)? = null,
    isError: Boolean = false,
    visualTransformation: VisualTransformation = VisualTransformation.None,
    keyboardOptions: KeyboardOptions = KeyboardOptions.Default,
    keyboardActions: KeyboardActions = KeyboardActions.Default,
    singleLine: Boolean = false,
    maxLines: Int = if (singleLine) 1 else Int.MAX_VALUE,
    minLines: Int = 1,
    interactionSource: MutableInteractionSource? = null,
    shape: Shape = TextFieldDefaults.shape,
    colors: TextFieldColors = TextFieldDefaults.colors(),
) {
    if (!look.web) return M3TextField(value, onValueChange, modifier, enabled, readOnly, textStyle, label, placeholder, leadingIcon,
        trailingIcon, prefix, suffix, supportingText, isError, visualTransformation, keyboardOptions, keyboardActions, singleLine, maxLines,
        minLines, interactionSource, shape, colors)
    val source = interactionSource ?: remember { MutableInteractionSource() }
    val focused by source.collectIsFocusedAsState()
    val defaults = TextFieldDefaults.colors()
    BasicTextField(value, onValueChange, modifier, enabled, readOnly, inlineStyle(textStyle, singleLine), keyboardOptions, keyboardActions,
        singleLine, maxLines, minLines, visualTransformation, interactionSource = source,
        cursorBrush = SolidColor(if (isError) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary),
        decorationBox = { inner ->
            InlineDecoration(focused, isError, enabled, colors, defaults, shape, singleLine, value.text.isEmpty(), label, placeholder, leadingIcon,
                trailingIcon, prefix, suffix, supportingText, inner)
        })
}

@Composable
private fun inlineStyle(textStyle: TextStyle, singleLine: Boolean): TextStyle {
    val base = if (textStyle == LocalTextStyle.current) textStyle.merge(WebTheme.style(if (singleLine) WebTokens.Search else WebTokens.MoreRow))
        else textStyle
    return base.copy(color = if (base.color == Color.Unspecified) look.palette.text else base.color)
}

@Composable
private fun InlineDecoration(
    focused: Boolean, isError: Boolean, enabled: Boolean, colors: TextFieldColors, defaults: TextFieldColors, shape: Shape,
    singleLine: Boolean, empty: Boolean,
    label: @Composable (() -> Unit)?, placeholder: @Composable (() -> Unit)?, leadingIcon: @Composable (() -> Unit)?,
    trailingIcon: @Composable (() -> Unit)?, prefix: @Composable (() -> Unit)?, suffix: @Composable (() -> Unit)?,
    supportingText: @Composable (() -> Unit)?, inner: @Composable () -> Unit,
) {
    val p = look.palette
    val cs = MaterialTheme.colorScheme
    val screenFill = when {
        isError -> colors.errorContainerColor
        focused -> colors.focusedContainerColor
        else -> colors.unfocusedContainerColor
    }
    val filled = screenFill.alpha > 0f
    // Material's own filled default becomes the web's input fill; a colour of the screen's stays.
    val fill = when {
        !filled -> Color.Transparent
        screenFill == defaults.focusedContainerColor || screenFill == defaults.unfocusedContainerColor ||
            screenFill == defaults.errorContainerColor -> if (focused) p.inputBgFocus else p.inputBg
        else -> screenFill
    }
    val boxShape = if (shape == TextFieldDefaults.shape) RoundedCornerShape(WebTokens.RadiusSm) else shape
    val screenLine = when {
        isError -> colors.errorIndicatorColor
        focused -> colors.focusedIndicatorColor
        else -> colors.unfocusedIndicatorColor
    }
    val line = when {
        filled || screenLine.alpha == 0f -> null
        isError -> cs.error
        focused -> cs.primary
        else -> p.border
    }
    val lineWidth = if (isError || focused) 2.dp else 1.dp
    Column(Modifier.alpha(if (enabled) 1f else 0.6f)) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 48.dp)
                .then(if (filled) Modifier.background(fill, boxShape) else Modifier)
                .padding(start = if (leadingIcon != null) 4.dp else 16.dp, end = if (trailingIcon != null) 4.dp else 16.dp),
            verticalAlignment = if (singleLine) Alignment.CenterVertically else Alignment.Top,
        ) {
            CompositionLocalProvider(LocalContentColor provides p.text2) { leadingIcon?.invoke() }
            Column(
                Modifier.weight(1f)
                    .padding(start = if (leadingIcon != null) 4.dp else 0.dp, end = if (trailingIcon != null) 4.dp else 0.dp)
                    .then(if (line != null) Modifier.drawBehind {
                        val w = lineWidth.toPx()
                        drawLine(line, Offset(0f, size.height - w / 2), Offset(size.width, size.height - w / 2), w)
                    } else Modifier)
                    .padding(top = 10.dp, bottom = if (line != null) 8.dp else 10.dp),
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                if (label != null) CompositionLocalProvider(LocalContentColor provides if (isError) cs.error else p.text2) {
                    ProvideTextStyle(WebTheme.style(WebTokens.FieldLabel, LocalTextStyle.current)) { label() }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    if (prefix != null) CompositionLocalProvider(LocalContentColor provides p.text2) { prefix() }
                    Box(Modifier.weight(1f, fill = false)) {
                        if (empty && placeholder != null) CompositionLocalProvider(LocalContentColor provides p.text3) { placeholder() }
                        inner()
                    }
                    if (suffix != null) CompositionLocalProvider(LocalContentColor provides p.text2) { suffix() }
                }
            }
            CompositionLocalProvider(LocalContentColor provides p.text2) { trailingIcon?.invoke() }
        }
        if (supportingText != null) CompositionLocalProvider(LocalContentColor provides if (isError) cs.error else p.text3) {
            ProvideTextStyle(WebTheme.style(WebTokens.FieldHint, LocalTextStyle.current)) {
                Box(Modifier.padding(start = 16.dp, end = 16.dp, top = 4.dp)) { supportingText() }
            }
        }
    }
}

// ---------------------------------------------------------------- select field

/**
 * A choice that opens a menu (the screen anchors its DropdownMenu next to it). In the web's look,
 * components.css .field with a .select: [label] above a 40 dp box with a 1 px border, 8 dp corners and
 * the caret at the end side; [value] in the box, [supporting] under it in the secondary text colour.
 * In Material's look, a list row with the label as its overline and an expand icon. The whole field is
 * one target with Role.DropdownList, so TalkBack reads the label with the value.
 */
@Composable
fun SelectField(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    onClickLabel: String? = null,
    leadingIcon: @Composable (() -> Unit)? = null,
    supporting: @Composable (() -> Unit)? = null,
    value: @Composable () -> Unit,
) {
    if (!look.web) return ListItem(headlineContent = value, overlineContent = { Text(label) }, supportingContent = supporting,
        leadingContent = leadingIcon, trailingContent = { Icon(Icons.Outlined.ExpandMore, null) },
        modifier = modifier.clickable(onClickLabel = onClickLabel, onClick = onClick))
    val p = look.palette
    Column(
        modifier.fillMaxWidth().clickable(onClickLabel = onClickLabel, role = Role.DropdownList, onClick = onClick).padding(vertical = 2.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text(label, style = WebTheme.style(WebTokens.FieldLabel), color = p.text2)
        Row(
            Modifier.fillMaxWidth().heightIn(min = 40.dp)
                .background(MaterialTheme.colorScheme.surface, RoundedCornerShape(WebTokens.RadiusSm))
                .border(1.dp, p.border, RoundedCornerShape(WebTokens.RadiusSm))
                .padding(start = 12.dp, end = 8.dp, top = 6.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (leadingIcon != null) {
                CompositionLocalProvider(LocalContentColor provides p.text2) {
                    Box(Modifier.size(20.dp), contentAlignment = Alignment.Center) { leadingIcon() }
                }
                Spacer(Modifier.width(10.dp))
            }
            Column(Modifier.weight(1f)) {
                CompositionLocalProvider(LocalContentColor provides p.text) {
                    ProvideTextStyle(WebTheme.style(WebTokens.Body, LocalTextStyle.current)) { value() }
                }
                if (supporting != null) CompositionLocalProvider(LocalContentColor provides p.text2) {
                    ProvideTextStyle(WebTheme.style(WebTokens.FieldHint, LocalTextStyle.current)) { supporting() }
                }
            }
            Icon(Icons.Outlined.ExpandMore, null, Modifier.size(20.dp), tint = p.text2)
        }
    }
}
