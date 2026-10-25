package io.github.murtazanasir.mailr.look

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.background
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialogDefaults
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.BottomSheetDefaults
import androidx.compose.material3.ChipColors
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.InputChipDefaults
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MenuDefaults
import androidx.compose.material3.MenuItemColors
import androidx.compose.material3.NavigationDrawerItemColors
import androidx.compose.material3.ProvideTextStyle
import androidx.compose.material3.SelectableChipColors
import androidx.compose.material3.SheetState
import androidx.compose.material3.TopAppBarColors
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.TopAppBarScrollBehavior
import androidx.compose.material3.contentColorFor
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.foundation.border
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.DpOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.window.PopupProperties
import androidx.compose.material3.AlertDialog as M3AlertDialog
import androidx.compose.material3.AssistChip as M3AssistChip
import androidx.compose.material3.DropdownMenu as M3DropdownMenu
import androidx.compose.material3.DropdownMenuItem as M3DropdownMenuItem
import androidx.compose.material3.FilterChip as M3FilterChip
import androidx.compose.material3.InputChip as M3InputChip
import androidx.compose.material3.ModalBottomSheet as M3ModalBottomSheet
import androidx.compose.material3.NavigationDrawerItem as M3NavigationDrawerItem
import androidx.compose.material3.TopAppBar as M3TopAppBar

/*
 * mailr's components. Each has the name and
 * the parameters of the Material 3 component it stands for, so a screen switches by its import alone.
 * In Material's look ([Look.web] false) each one is that component, called with what the screen
 * passed and Material's own defaults. In the web's look the defaults change to the web's (colours,
 * type, sizes from WebTokens); a screen's own choices still win. Material's mechanics stay: 48 dp
 * touch targets, TalkBack roles, back handling, sheet drag.
 */

/** A text style one step lighter: the web sets labels, chips and menu items in its regular weight. */
@Composable
private fun Regular(content: @Composable () -> Unit) =
    ProvideTextStyle(LocalTextStyle.current.copy(fontWeight = FontWeight.Normal), content)

// ---------------------------------------------------------------- top bar

/** The web's view header: flat, the page's surface, scrolled or not. */
@Composable
fun webTopAppBarColors(): TopAppBarColors = MaterialTheme.colorScheme.surface.let { s ->
    TopAppBarDefaults.topAppBarColors(containerColor = s, scrolledContainerColor = s)
}

@Composable
fun TopAppBar(
    title: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    navigationIcon: @Composable () -> Unit = {},
    actions: @Composable RowScope.() -> Unit = {},
    expandedHeight: Dp = TopAppBarDefaults.TopAppBarExpandedHeight,
    windowInsets: WindowInsets = TopAppBarDefaults.windowInsets,
    colors: TopAppBarColors = if (look.web) webTopAppBarColors() else TopAppBarDefaults.topAppBarColors(),
    scrollBehavior: TopAppBarScrollBehavior? = null,
) = M3TopAppBar(title = title, modifier = modifier, navigationIcon = navigationIcon, actions = actions,
    expandedHeight = expandedHeight, windowInsets = windowInsets, colors = colors, scrollBehavior = scrollBehavior)

// ---------------------------------------------------------------- menus

@Composable
fun DropdownMenu(
    expanded: Boolean,
    onDismissRequest: () -> Unit,
    modifier: Modifier = Modifier,
    offset: DpOffset = DpOffset(0.dp, 0.dp),
    scrollState: ScrollState = rememberScrollState(),
    properties: PopupProperties = PopupProperties(focusable = true),
    shape: Shape = MenuDefaults.shape,
    // The web's menu: the page's surface in light, a step up in dark (components.css .popover).
    containerColor: Color = if (look.web) look.palette.let { if (look.dark) it.surfaceContainer else it.surface } else MenuDefaults.containerColor,
    tonalElevation: Dp = MenuDefaults.TonalElevation,
    shadowElevation: Dp = MenuDefaults.ShadowElevation,
    border: BorderStroke? = null,
    content: @Composable ColumnScope.() -> Unit,
) = M3DropdownMenu(expanded = expanded, onDismissRequest = onDismissRequest, modifier = modifier, offset = offset,
    scrollState = scrollState, properties = properties, shape = shape, containerColor = containerColor,
    tonalElevation = tonalElevation, shadowElevation = shadowElevation, border = border, content = content)

@Composable
fun DropdownMenuItem(
    text: @Composable () -> Unit,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    leadingIcon: @Composable (() -> Unit)? = null,
    trailingIcon: @Composable (() -> Unit)? = null,
    enabled: Boolean = true,
    colors: MenuItemColors = MenuDefaults.itemColors(),
    contentPadding: PaddingValues = if (look.web) PaddingValues(horizontal = 16.dp) else MenuDefaults.DropdownMenuItemContentPadding,
    interactionSource: MutableInteractionSource? = null,
) {
    if (!look.web) return M3DropdownMenuItem(text = text, onClick = onClick, modifier = modifier, leadingIcon = leadingIcon,
        trailingIcon = trailingIcon, enabled = enabled, colors = colors, contentPadding = contentPadding, interactionSource = interactionSource)
    // components.css .menu-item: 14 px regular, 18 px icons in the secondary text colour.
    val p = look.palette
    M3DropdownMenuItem(text = { Regular(text) }, onClick = onClick, modifier = modifier,
        leadingIcon = leadingIcon?.let { icon ->
            { Box(Modifier.size(20.dp), contentAlignment = Alignment.Center) { CompositionLocalProvider(LocalContentColor provides p.text2) { icon() } } }
        },
        trailingIcon = trailingIcon, enabled = enabled, colors = colors, contentPadding = contentPadding, interactionSource = interactionSource)
}

// ---------------------------------------------------------------- sheets and dialogs

@Composable
fun ModalBottomSheet(
    onDismissRequest: () -> Unit,
    modifier: Modifier = Modifier,
    sheetState: SheetState = rememberModalBottomSheetState(),
    sheetMaxWidth: Dp = BottomSheetDefaults.SheetMaxWidth,
    shape: Shape = BottomSheetDefaults.ExpandedShape,
    // phone.css .bottom-sheet: the surface container.
    containerColor: Color = if (look.web) MaterialTheme.colorScheme.surfaceContainer else BottomSheetDefaults.ContainerColor,
    contentColor: Color = contentColorFor(containerColor),
    scrimColor: Color = BottomSheetDefaults.ScrimColor,
    dragHandle: @Composable (() -> Unit)? = { BottomSheetDefaults.DragHandle() },
    content: @Composable ColumnScope.() -> Unit,
) = M3ModalBottomSheet(onDismissRequest = onDismissRequest, modifier = modifier, sheetState = sheetState, sheetMaxWidth = sheetMaxWidth,
    shape = shape, containerColor = containerColor, contentColor = contentColor, scrimColor = scrimColor, dragHandle = dragHandle,
    content = content)

@Composable
fun AlertDialog(
    onDismissRequest: () -> Unit,
    confirmButton: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    dismissButton: @Composable (() -> Unit)? = null,
    icon: @Composable (() -> Unit)? = null,
    title: @Composable (() -> Unit)? = null,
    text: @Composable (() -> Unit)? = null,
    shape: Shape = AlertDialogDefaults.shape,
    // components.css .dialog: the page's surface in light, the surface container in dark.
    containerColor: Color = if (look.web) look.palette.let { if (look.dark) it.surfaceContainer else it.surface } else AlertDialogDefaults.containerColor,
    iconContentColor: Color = AlertDialogDefaults.iconContentColor,
    titleContentColor: Color = AlertDialogDefaults.titleContentColor,
    textContentColor: Color = AlertDialogDefaults.textContentColor,
    tonalElevation: Dp = AlertDialogDefaults.TonalElevation,
    properties: DialogProperties = DialogProperties(),
) = M3AlertDialog(onDismissRequest = onDismissRequest, confirmButton = confirmButton, modifier = modifier, dismissButton = dismissButton,
    icon = icon, title = title, text = text, shape = shape, containerColor = containerColor, iconContentColor = iconContentColor,
    titleContentColor = titleContentColor, textContentColor = textContentColor, tonalElevation = tonalElevation, properties = properties)

// ---------------------------------------------------------------- chips

@Composable
fun FilterChip(
    selected: Boolean,
    onClick: () -> Unit,
    label: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    leadingIcon: @Composable (() -> Unit)? = null,
    trailingIcon: @Composable (() -> Unit)? = null,
    shape: Shape = FilterChipDefaults.shape,
    colors: SelectableChipColors = FilterChipDefaults.filterChipColors(),
    border: BorderStroke? = FilterChipDefaults.filterChipBorder(enabled, selected),
    interactionSource: MutableInteractionSource? = null,
) = M3FilterChip(selected = selected, onClick = onClick, label = if (look.web) ({ Regular(label) }) else label, modifier = modifier,
    enabled = enabled, leadingIcon = leadingIcon, trailingIcon = trailingIcon, shape = shape, colors = colors, border = border,
    interactionSource = interactionSource)

@Composable
fun AssistChip(
    onClick: () -> Unit,
    label: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    leadingIcon: @Composable (() -> Unit)? = null,
    trailingIcon: @Composable (() -> Unit)? = null,
    shape: Shape = AssistChipDefaults.shape,
    colors: ChipColors = AssistChipDefaults.assistChipColors(),
    border: BorderStroke? = AssistChipDefaults.assistChipBorder(enabled),
    interactionSource: MutableInteractionSource? = null,
) = M3AssistChip(onClick = onClick, label = if (look.web) ({ Regular(label) }) else label, modifier = modifier, enabled = enabled,
    leadingIcon = leadingIcon, trailingIcon = trailingIcon, shape = shape, colors = colors, border = border,
    interactionSource = interactionSource)

@Composable
fun InputChip(
    selected: Boolean,
    onClick: () -> Unit,
    label: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    leadingIcon: @Composable (() -> Unit)? = null,
    avatar: @Composable (() -> Unit)? = null,
    trailingIcon: @Composable (() -> Unit)? = null,
    shape: Shape = InputChipDefaults.shape,
    colors: SelectableChipColors = InputChipDefaults.inputChipColors(),
    border: BorderStroke? = InputChipDefaults.inputChipBorder(enabled, selected),
    interactionSource: MutableInteractionSource? = null,
) = M3InputChip(selected = selected, onClick = onClick, label = if (look.web) ({ Regular(label) }) else label, modifier = modifier,
    enabled = enabled, leadingIcon = leadingIcon, avatar = avatar, trailingIcon = trailingIcon, shape = shape, colors = colors,
    border = border, interactionSource = interactionSource)

// ---------------------------------------------------------------- drawer rows

/**
 * A drawer row. In the web's look, layout.css .nav-item: 14 px names (bold when current), the current
 * row on the navigation colour with its end side rounded, 48 dp tall for touch.
 */
@Composable
fun NavigationDrawerItem(
    label: @Composable () -> Unit,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    icon: @Composable (() -> Unit)? = null,
    badge: @Composable (() -> Unit)? = null,
    colors: NavigationDrawerItemColors = androidx.compose.material3.NavigationDrawerItemDefaults.colors(),
    interactionSource: MutableInteractionSource? = null,
) {
    if (!look.web) return M3NavigationDrawerItem(label = label, selected = selected, onClick = onClick, modifier = modifier, icon = icon,
        badge = badge, colors = colors, interactionSource = interactionSource)
    val p = look.palette
    val fg = if (selected) MaterialTheme.colorScheme.onSecondaryContainer else p.text
    Row(
        modifier
            .heightIn(min = 44.dp)
            .fillMaxWidth()
            .background(if (selected) MaterialTheme.colorScheme.secondaryContainer else Color.Transparent,
                RoundedCornerShape(topEndPercent = 50, bottomEndPercent = 50))
            .selectable(selected = selected, onClick = onClick, role = Role.Tab, interactionSource = interactionSource, indication = androidx.compose.material3.ripple())
            .padding(start = 26.dp, end = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        CompositionLocalProvider(LocalContentColor provides if (selected) fg else p.text2) {
            if (icon != null) {
                Box(Modifier.size(20.dp), contentAlignment = Alignment.Center) { icon() }
                Spacer(Modifier.width(18.dp))
            }
        }
        CompositionLocalProvider(LocalContentColor provides fg) {
            Box(Modifier.weight(1f)) {
                ProvideTextStyle(WebTheme.style(WebTokens.NavItem, LocalTextStyle.current)
                    .copy(fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal)) { label() }
            }
            if (badge != null) {
                Spacer(Modifier.width(8.dp))
                ProvideTextStyle(WebTheme.style(WebTokens.NavCount, LocalTextStyle.current)) { badge() }
            }
        }
    }
}

/** Space between rows of a web-style list: the web's 1 px divider. */
@Composable
fun WebDivider(modifier: Modifier = Modifier) =
    Box(modifier.fillMaxWidth().heightIn(min = 1.dp, max = 1.dp).background(look.palette.divider))

/** The web's 1 px lines drawn over a box's own background (a row's bottom divider, a bar's top border). */
object Hairline {
    fun bottom(color: Color): Modifier = Modifier.drawWithContent {
        drawContent()
        val y = size.height - 0.5.dp.toPx()
        drawLine(color, androidx.compose.ui.geometry.Offset(0f, y), androidx.compose.ui.geometry.Offset(size.width, y), 1.dp.toPx())
    }

    fun top(color: Color): Modifier = Modifier.drawWithContent {
        drawContent()
        val y = 0.5.dp.toPx()
        drawLine(color, androidx.compose.ui.geometry.Offset(0f, y), androidx.compose.ui.geometry.Offset(size.width, y), 1.dp.toPx())
    }
}

// ---------------------------------------------------------------- switches and boxes

/**
 * A switch. In the web's look, components.css .switch: a 36 × 20 track (the primary colour when on,
 * a outlined neutral when off) with a 14 dp knob, inside a 48 dp touch target.
 */
@Composable
fun Switch(
    checked: Boolean,
    onCheckedChange: ((Boolean) -> Unit)?,
    modifier: Modifier = Modifier,
    thumbContent: (@Composable () -> Unit)? = null,
    enabled: Boolean = true,
    colors: androidx.compose.material3.SwitchColors = androidx.compose.material3.SwitchDefaults.colors(),
    interactionSource: MutableInteractionSource? = null,
) {
    if (!look.web) return androidx.compose.material3.Switch(checked = checked, onCheckedChange = onCheckedChange, modifier = modifier,
        thumbContent = thumbContent, enabled = enabled, colors = colors, interactionSource = interactionSource)
    val p = look.palette
    val cs = MaterialTheme.colorScheme
    val toggle = if (onCheckedChange != null) Modifier.toggleable(checked, interactionSource = interactionSource,
        indication = androidx.compose.material3.ripple(bounded = false, radius = 24.dp), enabled = enabled, role = Role.Switch,
        onValueChange = onCheckedChange) else Modifier
    Box(modifier.then(if (onCheckedChange != null) Modifier.minimumInteractiveComponentSize() else Modifier).then(toggle)
        .alpha(if (enabled) 1f else 0.4f), contentAlignment = Alignment.Center) {
        Box(Modifier.size(36.dp, 20.dp)
            .background(if (checked) cs.primary else p.surfaceHigh, CircleShapeFull)
            .then(if (checked) Modifier else Modifier.border(1.dp, p.text3, CircleShapeFull))
            .padding(3.dp),
            contentAlignment = if (checked) Alignment.CenterEnd else Alignment.CenterStart) {
            Box(Modifier.size(14.dp).background(if (checked) cs.onPrimary else p.text2, CircleShapeFull))
        }
    }
}

private val CircleShapeFull = RoundedCornerShape(50)

/** A check box. In the web's look, components.css .checkbox: the text colour when ticked, a 2 dp text-2 outline when not. */
@Composable
fun Checkbox(
    checked: Boolean,
    onCheckedChange: ((Boolean) -> Unit)?,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    colors: androidx.compose.material3.CheckboxColors = if (look.web) androidx.compose.material3.CheckboxDefaults.colors(
        checkedColor = look.palette.text, uncheckedColor = look.palette.text2, checkmarkColor = look.palette.bg,
    ) else androidx.compose.material3.CheckboxDefaults.colors(),
    interactionSource: MutableInteractionSource? = null,
) = androidx.compose.material3.Checkbox(checked = checked, onCheckedChange = onCheckedChange, modifier = modifier, enabled = enabled,
    colors = colors, interactionSource = interactionSource)

// ---------------------------------------------------------------- the Compose button

/** The extended floating button. In the web's look, layout.css .compose-fab: the compose colours. */
@Composable
fun ExtendedFloatingActionButton(
    text: @Composable () -> Unit,
    icon: @Composable () -> Unit,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    expanded: Boolean = true,
    shape: Shape = androidx.compose.material3.FloatingActionButtonDefaults.extendedFabShape,
    containerColor: Color = if (look.web) look.palette.composeBg else androidx.compose.material3.FloatingActionButtonDefaults.containerColor,
    contentColor: Color = if (look.web) look.palette.composeFg else contentColorFor(containerColor),
    elevation: androidx.compose.material3.FloatingActionButtonElevation = androidx.compose.material3.FloatingActionButtonDefaults.elevation(),
    interactionSource: MutableInteractionSource? = null,
) = androidx.compose.material3.ExtendedFloatingActionButton(text = text, icon = icon, onClick = onClick, modifier = modifier,
    expanded = expanded, shape = shape, containerColor = containerColor, contentColor = contentColor, elevation = elevation,
    interactionSource = interactionSource)

/**
 * Material's drawer-row defaults with the web's spacing: in the web's look a row runs from the
 * drawer's start edge (layout.css .nav-item) with only the end side inset.
 */
object NavigationDrawerItemDefaults {
    val ItemPadding: PaddingValues
        @Composable get() = if (look.web) PaddingValues(end = 12.dp) else androidx.compose.material3.NavigationDrawerItemDefaults.ItemPadding

}
