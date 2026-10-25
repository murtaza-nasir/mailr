package io.github.murtazanasir.mailr.look

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.ButtonColors
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ButtonElevation
import androidx.compose.material3.CardColors
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CardElevation
import androidx.compose.material3.ListItemColors
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.ProvideTextStyle
import androidx.compose.material3.Surface
import androidx.compose.material3.TextFieldColors
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.material3.Button as M3Button
import androidx.compose.material3.Card as M3Card
import androidx.compose.material3.FilledTonalButton as M3FilledTonalButton
import androidx.compose.material3.ListItem as M3ListItem
import androidx.compose.material3.OutlinedButton as M3OutlinedButton
import androidx.compose.material3.OutlinedCard as M3OutlinedCard
import androidx.compose.material3.OutlinedTextField as M3OutlinedTextField
import androidx.compose.material3.TextButton as M3TextButton

/*
 * More of mailr's stand-ins (Components.kt has the rest): buttons, list rows, fields and cards. Each
 * keeps the name and parameters of its Material 3 component; in Material's look it is that component.
 */

// ---------------------------------------------------------------- buttons

/** components.css .btn: 36 dp tall, fully rounded, 14 px medium, no shadow; inside a 48 dp touch target. */
@Composable
private fun WebButton(
    onClick: () -> Unit, modifier: Modifier, enabled: Boolean, shape: Shape, container: Color, content: Color,
    border: BorderStroke?, contentPadding: PaddingValues, interactionSource: MutableInteractionSource?, body: @Composable RowScope.() -> Unit,
) {
    Surface(onClick = onClick, modifier = modifier.semantics { role = Role.Button }, enabled = enabled, shape = shape,
        color = container, contentColor = content, border = border, interactionSource = interactionSource) {
        ProvideTextStyle(WebTheme.style(WebTokens.Button, LocalTextStyle.current)) {
            Row(Modifier.defaultMinSize(minWidth = 48.dp, minHeight = 36.dp).alpha(if (enabled) 1f else 0.5f).padding(contentPadding),
                horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.CenterHorizontally),
                verticalAlignment = Alignment.CenterVertically, content = body)
        }
    }
}

private val Pill = RoundedCornerShape(50)

/** The web's button colours: [container] and [content] when enabled; disabled ones are the same at half strength. */
private fun webButtonColors(container: Color, content: Color) = ButtonColors(container, content, container, content)

@Composable
fun Button(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    shape: Shape = if (look.web) Pill else ButtonDefaults.shape,
    colors: ButtonColors = if (look.web) webButtonColors(MaterialTheme.colorScheme.primary, MaterialTheme.colorScheme.onPrimary) else ButtonDefaults.buttonColors(),
    elevation: ButtonElevation? = ButtonDefaults.buttonElevation(),
    border: BorderStroke? = null,
    contentPadding: PaddingValues = if (look.web) PaddingValues(horizontal = 16.dp) else ButtonDefaults.ContentPadding,
    interactionSource: MutableInteractionSource? = null,
    content: @Composable RowScope.() -> Unit,
) {
    if (!look.web) return M3Button(onClick, modifier, enabled, shape, colors, elevation, border, contentPadding, interactionSource, content)
    WebButton(onClick, modifier, enabled, shape, colors.containerColor, colors.contentColor, border, contentPadding, interactionSource, content)
}

@Composable
fun FilledTonalButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    shape: Shape = if (look.web) Pill else ButtonDefaults.filledTonalShape,
    colors: ButtonColors = if (look.web) webButtonColors(MaterialTheme.colorScheme.primaryContainer, MaterialTheme.colorScheme.onPrimaryContainer)
        else ButtonDefaults.filledTonalButtonColors(),
    elevation: ButtonElevation? = ButtonDefaults.filledTonalButtonElevation(),
    border: BorderStroke? = null,
    contentPadding: PaddingValues = if (look.web) PaddingValues(horizontal = 16.dp) else ButtonDefaults.ContentPadding,
    interactionSource: MutableInteractionSource? = null,
    content: @Composable RowScope.() -> Unit,
) {
    if (!look.web) return M3FilledTonalButton(onClick, modifier, enabled, shape, colors, elevation, border, contentPadding, interactionSource, content)
    WebButton(onClick, modifier, enabled, shape, colors.containerColor, colors.contentColor, border, contentPadding, interactionSource, content)
}

@Composable
fun OutlinedButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    shape: Shape = if (look.web) Pill else ButtonDefaults.outlinedShape,
    colors: ButtonColors = if (look.web) webButtonColors(Color.Transparent, MaterialTheme.colorScheme.primary) else ButtonDefaults.outlinedButtonColors(),
    elevation: ButtonElevation? = null,
    // components.css .btn.outlined: a 1 px line in the border colour.
    border: BorderStroke? = if (look.web) BorderStroke(1.dp, look.palette.border) else ButtonDefaults.outlinedButtonBorder(enabled),
    contentPadding: PaddingValues = if (look.web) PaddingValues(horizontal = 16.dp) else ButtonDefaults.ContentPadding,
    interactionSource: MutableInteractionSource? = null,
    content: @Composable RowScope.() -> Unit,
) {
    if (!look.web) return M3OutlinedButton(onClick, modifier, enabled, shape, colors, elevation, border, contentPadding, interactionSource, content)
    WebButton(onClick, modifier, enabled, shape, colors.containerColor, colors.contentColor, border, contentPadding, interactionSource, content)
}

@Composable
fun TextButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    shape: Shape = if (look.web) Pill else ButtonDefaults.textShape,
    colors: ButtonColors = if (look.web) webButtonColors(Color.Transparent, MaterialTheme.colorScheme.primary) else ButtonDefaults.textButtonColors(),
    elevation: ButtonElevation? = null,
    border: BorderStroke? = null,
    contentPadding: PaddingValues = if (look.web) PaddingValues(horizontal = 12.dp) else ButtonDefaults.TextButtonContentPadding,
    interactionSource: MutableInteractionSource? = null,
    content: @Composable RowScope.() -> Unit,
) {
    if (!look.web) return M3TextButton(onClick, modifier, enabled, shape, colors, elevation, border, contentPadding, interactionSource, content)
    WebButton(onClick, modifier, enabled, shape, colors.containerColor, colors.contentColor, border, contentPadding, interactionSource, content)
}

// ---------------------------------------------------------------- list rows

/**
 * Whether web-style [ListItem]s draw the web's 1 px divider under each row (settings.css .set-row).
 * A settings page provides true; rows elsewhere (sheets, menus) have none, as on the web.
 */
val LocalRowDividers = staticCompositionLocalOf { false }

/**
 * A list row. In the web's look, phone.css .more-row and settings.css .set-row: 15 px names, 13 px
 * secondary lines in the secondary text colour, 16 dp sides, at least 52 dp tall (64 with a second
 * line), flat, with the web's divider where [LocalRowDividers] asks for it.
 */
@Composable
fun ListItem(
    headlineContent: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    overlineContent: @Composable (() -> Unit)? = null,
    supportingContent: @Composable (() -> Unit)? = null,
    leadingContent: @Composable (() -> Unit)? = null,
    trailingContent: @Composable (() -> Unit)? = null,
    colors: ListItemColors = ListItemDefaults.colors(),
    tonalElevation: Dp = ListItemDefaults.Elevation,
    shadowElevation: Dp = ListItemDefaults.Elevation,
) {
    if (!look.web) return M3ListItem(headlineContent, modifier, overlineContent, supportingContent, leadingContent, trailingContent,
        colors, tonalElevation, shadowElevation)
    val p = look.palette
    val divided = LocalRowDividers.current
    Row(
        modifier
            .fillMaxWidth()
            .background(colors.containerColor)
            .then(if (divided) Hairline.bottom(p.divider) else Modifier)
            .heightIn(min = if (supportingContent != null || overlineContent != null) 64.dp else 52.dp)
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (leadingContent != null) {
            CompositionLocalProvider(LocalContentColor provides colors.leadingIconColor) { leadingContent() }
            androidx.compose.foundation.layout.Spacer(Modifier.padding(start = 16.dp))
        }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            if (overlineContent != null) CompositionLocalProvider(LocalContentColor provides colors.overlineColor) {
                ProvideTextStyle(WebTheme.style(WebTokens.FieldLabel, LocalTextStyle.current)) { overlineContent() }
            }
            CompositionLocalProvider(LocalContentColor provides colors.headlineColor) {
                ProvideTextStyle(WebTheme.style(WebTokens.MoreRow, LocalTextStyle.current)) { headlineContent() }
            }
            if (supportingContent != null) CompositionLocalProvider(LocalContentColor provides colors.supportingTextColor) {
                ProvideTextStyle(LocalTextStyle.current.copy(fontSize = 13.sp, lineHeight = 18.sp, fontWeight = androidx.compose.ui.text.font.FontWeight.Normal,
                    letterSpacing = 0.sp)) { supportingContent() }
            }
        }
        if (trailingContent != null) {
            androidx.compose.foundation.layout.Spacer(Modifier.padding(start = 16.dp))
            CompositionLocalProvider(LocalContentColor provides colors.trailingIconColor) {
                ProvideTextStyle(WebTheme.style(WebTokens.RowDate, LocalTextStyle.current)) { trailingContent() }
            }
        }
    }
}

// ---------------------------------------------------------------- fields

/**
 * components.css .field with an .input: the label above the box (12 px medium, the secondary text
 * colour), a 40 dp box with a 1 px border that turns primary when focused, 8 dp corners, the hint
 * or error below. Label and hint are the text field's own decoration, as in Material, so TalkBack
 * reads them with the field and a tap on the label focuses it. The field takes the width it is
 * given, as the web's .input (width: 100%).
 */
@Composable
private fun WebField(
    label: @Composable (() -> Unit)?, supportingText: @Composable (() -> Unit)?, isError: Boolean, enabled: Boolean,
    box: @Composable () -> Unit,
) {
    val p = look.palette
    val cs = MaterialTheme.colorScheme
    Column(Modifier.fillMaxWidth().alpha(if (enabled) 1f else 0.6f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        if (label != null) CompositionLocalProvider(LocalContentColor provides if (isError) cs.error else p.text2) {
            ProvideTextStyle(WebTheme.style(WebTokens.FieldLabel, LocalTextStyle.current)) { label() }
        }
        box()
        if (supportingText != null) CompositionLocalProvider(LocalContentColor provides if (isError) cs.error else p.text3) {
            ProvideTextStyle(WebTheme.style(WebTokens.FieldHint, LocalTextStyle.current)) { supportingText() }
        }
    }
}

@Composable
private fun WebBox(
    focused: Boolean, isError: Boolean, shape: Shape, singleLine: Boolean, empty: Boolean,
    leadingIcon: @Composable (() -> Unit)?, trailingIcon: @Composable (() -> Unit)?, prefix: @Composable (() -> Unit)?,
    suffix: @Composable (() -> Unit)?, placeholder: @Composable (() -> Unit)?, inner: @Composable () -> Unit,
) {
    val p = look.palette
    val cs = MaterialTheme.colorScheme
    val line = when {
        isError -> cs.error
        focused -> cs.primary
        else -> p.border
    }
    Row(
        Modifier.fillMaxWidth().heightIn(min = 40.dp)
            .background(cs.surface, shape)
            .border(if (focused || isError) 2.dp else 1.dp, line, shape)
            .padding(start = if (leadingIcon != null) 4.dp else 12.dp, end = if (trailingIcon != null) 0.dp else 12.dp),
        verticalAlignment = if (singleLine) Alignment.CenterVertically else Alignment.Top,
    ) {
        CompositionLocalProvider(LocalContentColor provides p.text2) { leadingIcon?.invoke() }
        Box(Modifier.weight(1f).padding(vertical = 9.dp, horizontal = if (leadingIcon != null) 4.dp else 0.dp)) {
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
}

@Composable
fun OutlinedTextField(
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
    shape: Shape = OutlinedTextFieldDefaults.shape,
    colors: TextFieldColors = OutlinedTextFieldDefaults.colors(),
) {
    if (!look.web) return M3OutlinedTextField(value, onValueChange, modifier, enabled, readOnly, textStyle, label, placeholder, leadingIcon,
        trailingIcon, prefix, suffix, supportingText, isError, visualTransformation, keyboardOptions, keyboardActions, singleLine, maxLines,
        minLines, interactionSource, shape, colors)
    val source = interactionSource ?: remember { MutableInteractionSource() }
    val focused by source.collectIsFocusedAsState()
    val style = webFieldStyle(textStyle)
    val boxShape = webShape(shape)
    BasicTextField(value, onValueChange, modifier, enabled, readOnly, style, keyboardOptions, keyboardActions, singleLine, maxLines, minLines,
        visualTransformation, interactionSource = source, cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
        decorationBox = { inner ->
            WebField(label, supportingText, isError, enabled) {
                WebBox(focused, isError, boxShape, singleLine, value.isEmpty(), leadingIcon, trailingIcon, prefix, suffix, placeholder, inner)
            }
        })
}

@Composable
fun OutlinedTextField(
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
    shape: Shape = OutlinedTextFieldDefaults.shape,
    colors: TextFieldColors = OutlinedTextFieldDefaults.colors(),
) {
    if (!look.web) return M3OutlinedTextField(value, onValueChange, modifier, enabled, readOnly, textStyle, label, placeholder, leadingIcon,
        trailingIcon, prefix, suffix, supportingText, isError, visualTransformation, keyboardOptions, keyboardActions, singleLine, maxLines,
        minLines, interactionSource, shape, colors)
    val source = interactionSource ?: remember { MutableInteractionSource() }
    val focused by source.collectIsFocusedAsState()
    val style = webFieldStyle(textStyle)
    val boxShape = webShape(shape)
    BasicTextField(value, onValueChange, modifier, enabled, readOnly, style, keyboardOptions, keyboardActions, singleLine, maxLines, minLines,
        visualTransformation, interactionSource = source, cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
        decorationBox = { inner ->
            WebField(label, supportingText, isError, enabled) {
                WebBox(focused, isError, boxShape, singleLine, value.text.isEmpty(), leadingIcon, trailingIcon, prefix, suffix, placeholder, inner)
            }
        })
}

/** The field's text: the screen's style at the web's 14 px unless the screen set a size, in the text colour. */
@Composable
private fun webFieldStyle(textStyle: TextStyle): TextStyle {
    val base = if (textStyle == LocalTextStyle.current) textStyle.merge(WebTheme.style(WebTokens.Body)) else textStyle
    val text = look.palette.text
    return base.copy(color = if (base.color == Color.Unspecified) text else base.color)
}

/** Material's default field shape becomes the web's 8 dp; a screen's own shape stays. */
@Composable
private fun webShape(shape: Shape): Shape = if (shape == OutlinedTextFieldDefaults.shape) RoundedCornerShape(WebTokens.RadiusSm) else shape

// ---------------------------------------------------------------- cards

/** settings.css .card: a 1 px divider-coloured border, 12 dp corners, flat; the page's surface (a step up in dark). */
@Composable
fun webCardColors(): CardColors = look.palette.let { p ->
    val bg = if (look.dark) p.surfaceContainer else MaterialTheme.colorScheme.surface
    CardDefaults.cardColors(containerColor = bg, contentColor = p.text)
}

private val FlatCard: CardElevation @Composable get() = CardDefaults.cardElevation(0.dp, 0.dp, 0.dp, 0.dp, 0.dp, 0.dp)

@Composable
fun Card(
    modifier: Modifier = Modifier,
    shape: Shape = if (look.web) RoundedCornerShape(WebTokens.RadiusMd) else CardDefaults.shape,
    colors: CardColors = if (look.web) webCardColors() else CardDefaults.cardColors(),
    elevation: CardElevation = if (look.web) FlatCard else CardDefaults.cardElevation(),
    border: BorderStroke? = if (look.web) BorderStroke(1.dp, look.palette.divider) else null,
    content: @Composable ColumnScope.() -> Unit,
) = M3Card(modifier, shape, colors, elevation, border, content)

@Composable
fun Card(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    shape: Shape = if (look.web) RoundedCornerShape(WebTokens.RadiusMd) else CardDefaults.shape,
    colors: CardColors = if (look.web) webCardColors() else CardDefaults.cardColors(),
    elevation: CardElevation = if (look.web) FlatCard else CardDefaults.cardElevation(),
    border: BorderStroke? = if (look.web) BorderStroke(1.dp, look.palette.divider) else null,
    interactionSource: MutableInteractionSource? = null,
    content: @Composable ColumnScope.() -> Unit,
) = M3Card(onClick, modifier, enabled, shape, colors, elevation, border, interactionSource, content)

@Composable
fun OutlinedCard(
    modifier: Modifier = Modifier,
    shape: Shape = if (look.web) RoundedCornerShape(WebTokens.RadiusMd) else CardDefaults.outlinedShape,
    colors: CardColors = if (look.web) webCardColors() else CardDefaults.outlinedCardColors(),
    elevation: CardElevation = if (look.web) FlatCard else CardDefaults.outlinedCardElevation(),
    border: BorderStroke = if (look.web) BorderStroke(1.dp, look.palette.divider) else CardDefaults.outlinedCardBorder(),
    content: @Composable ColumnScope.() -> Unit,
) = M3OutlinedCard(modifier, shape, colors, elevation, border, content)
