package io.github.murtazanasir.mailr.ui.components

import androidx.compose.animation.core.exponentialDecay
import androidx.compose.animation.core.spring
import androidx.compose.foundation.gestures.AnchoredDraggableState
import androidx.compose.foundation.gestures.DraggableAnchors
import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.gestures.anchoredDraggable
import androidx.compose.foundation.gestures.animateTo
import androidx.compose.foundation.gestures.snapTo
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.offset
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Modifier
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.LayoutDirection
import kotlinx.coroutines.launch
import kotlin.math.abs
import kotlin.math.roundToInt

/** Shared by the swipe rows: how far a row must be dragged before letting go acts. */
object DeliberateSwipe {
    const val FRACTION = 0.45f
}

/**
 * A row that acts on a deliberate horizontal swipe (mail list, task list).
 *
 * Material's SwipeToDismissBox also accepts a fast fling from any distance (a fixed ~125 dp/s that
 * callers cannot change), so a slightly diagonal scroll archived mail or completed tasks. Here only
 * distance counts: the row must be dragged past [DeliberateSwipe.FRACTION] of its width; flings
 * never trigger. A haptic tick marks the line. [onSwipe] runs on release past the line and returns
 * true to leave the row swiped (the list is removing it), false to snap back.
 */
@Composable
fun SwipeRow(
    enableStart: Boolean,
    enableEnd: Boolean,
    onSwipe: (SwipeToDismissBoxValue) -> Boolean,
    background: @Composable (direction: SwipeToDismissBoxValue, armed: Boolean) -> Unit,
    modifier: Modifier = Modifier,
    resetKey: Any? = null,
    content: @Composable () -> Unit,
) {
    val haptics = LocalHapticFeedback.current
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    val scope = rememberCoroutineScope()
    val act by rememberUpdatedState(onSwipe)
    var width by remember { mutableFloatStateOf(0f) }
    val state = remember {
        AnchoredDraggableState(
            initialValue = SwipeToDismissBoxValue.Settled,
            positionalThreshold = { d -> d * DeliberateSwipe.FRACTION },
            velocityThreshold = { Float.POSITIVE_INFINITY }, // flings never jump to an action
            snapAnimationSpec = spring(),
            decayAnimationSpec = exponentialDecay(),
        )
    }
    LaunchedEffect(width, enableStart, enableEnd, rtl) {
        if (width <= 0f) return@LaunchedEffect
        // Offsets count toward the end: anchoredDraggable reverses a horizontal drag right to left,
        // and Modifier.offset mirrors it back on the screen, so the row follows the finger and a
        // swipe toward the end is StartToEnd in both directions.
        state.updateAnchors(DraggableAnchors {
            SwipeToDismissBoxValue.Settled at 0f
            if (enableStart) SwipeToDismissBoxValue.StartToEnd at width
            if (enableEnd) SwipeToDismissBoxValue.EndToStart at -width
        })
    }
    // The row's data changed (read state, star): whatever was swiped comes back.
    LaunchedEffect(resetKey) { if (state.settledValue != SwipeToDismissBoxValue.Settled) state.snapTo(SwipeToDismissBoxValue.Settled) }
    fun off(): Float = state.offset.let { if (it.isNaN()) 0f else it }
    val direction by remember {
        derivedStateOf {
            val o = off()
            when {
                o == 0f -> SwipeToDismissBoxValue.Settled
                o > 0f -> SwipeToDismissBoxValue.StartToEnd
                else -> SwipeToDismissBoxValue.EndToStart
            }
        }
    }
    val armed by remember { derivedStateOf { width > 0f && abs(off()) >= width * DeliberateSwipe.FRACTION } }
    LaunchedEffect(state) {
        var was = false
        snapshotFlow { armed }.collect { now ->
            if (now != was) haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
            was = now
        }
    }
    LaunchedEffect(state) {
        snapshotFlow { state.settledValue }.collect { v ->
            if (v != SwipeToDismissBoxValue.Settled && !act(v)) scope.launch { state.animateTo(SwipeToDismissBoxValue.Settled) }
        }
    }
    Box(modifier.onSizeChanged { width = it.width.toFloat() }) {
        Box(Modifier.matchParentSize()) { if (direction != SwipeToDismissBoxValue.Settled) background(direction, armed) }
        Box(Modifier.offset { IntOffset(off().roundToInt(), 0) }
            .anchoredDraggable(state, Orientation.Horizontal, enabled = enableStart || enableEnd)) {
            content()
        }
    }
}
