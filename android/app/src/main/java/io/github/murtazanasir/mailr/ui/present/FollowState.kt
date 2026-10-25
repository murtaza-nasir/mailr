package io.github.murtazanasir.mailr.ui.present

import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.interaction.DragInteraction
import androidx.compose.foundation.lazy.LazyListLayoutInfo
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch

/*
 * Follow/detach scrolling for the chat, the web's
 * useFollowScroll.ts on Android:
 * - attached while the end of the log is within 48 dp of the bottom of the view;
 * - detached only when the log moves UP (towards older output) and ends away from the bottom: a drag
 *   or fling up, or TalkBack's scroll actions. Growth below never moves the position, and following
 *   only ever moves the log down, so neither can detach it;
 * - "Jump to latest" shows only while detached AND the latest output is more than 150 dp below the
 *   view. Reaching the bottom again, or jump(), attaches.
 *
 * Following watches the layout, not only the turn's data: the reply is one list item that grows
 * (streamed text, steps, an approval card, animateContentSize), and the viewport shrinks when the
 * keyboard opens. Before 4 Oct 2026 only a change of the turn's data scrolled, once, before the item
 * had grown. Until 7 Oct a move seen after the follow's own scroll had finished could count as the
 * reader's, and a reply that grew in between then detached the log while the reader sat at the
 * bottom; the direction rule has no such race.
 */

/** The decisions, without Compose, for tests. */
class FollowLogic {
    var attached = true
        private set
    private var userScrolling = false

    /** The reader put a finger on the log (a drag; its fling follows until the scroll settles). */
    fun dragStarted() { userScrolling = true }

    /** The reader's scroll ended. [movedUp]: the log is above where the drag began. */
    fun scrollSettled(atBottom: Boolean, movedUp: Boolean = false) {
        if (atBottom) attached = true
        else if (userScrolling && movedUp) attached = false
        userScrolling = false
    }

    /** The log's position changed. [up]: towards older output. [atBottom]: where it is now. */
    fun moved(up: Boolean, atBottom: Boolean) {
        if (atBottom) attached = true
        else if (up) attached = false
    }

    fun jump() { attached = true }

    /** Stop following, so an earlier turn brought into view stays there (a memory's "From a chat"). */
    fun detach() { attached = false }

    /** The content no longer reaches the bottom of the screen ([atBottom] false) while attached and the
     *  user is not scrolling: scroll to the end again. */
    fun shouldFollow(atBottom: Boolean) = attached && !userScrolling && !atBottom

    /** The pill: detached, and the latest output is [far] out of sight. */
    fun showJump(far: Boolean) = !attached && far
}

private data class FollowSizes(val items: Int, val lastSize: Int?, val viewport: Int)

/** A list position, ordered from the start of the log: the first visible item and how far into it. */
internal data class ListPos(val index: Int, val into: Int) : Comparable<ListPos> {
    override fun compareTo(other: ListPos) = compareValuesBy(this, other, { it.index }, { it.into })
}

internal fun LazyListLayoutInfo.pos(): ListPos = visibleItemsInfo.firstOrNull().let { ListPos(it?.index ?: 0, -(it?.offset ?: 0)) }

/** How far the end of the last item is below the bottom of the view, in px (0 or less: in view). */
fun distanceToEnd(info: LazyListLayoutInfo): Int {
    val last = info.visibleItemsInfo.lastOrNull() ?: return 0
    if (last.index < info.totalItemsCount - 1) return Int.MAX_VALUE
    return last.offset + last.size - (info.viewportEndOffset - info.afterContentPadding)
}

/** True when the end of the last item is within [slackPx] of the viewport's end (or nothing to scroll). */
fun atBottom(info: LazyListLayoutInfo, slackPx: Int): Boolean = distanceToEnd(info) <= slackPx

class FollowState internal constructor(private val list: LazyListState) {
    internal val logic = FollowLogic()
    var attached by mutableStateOf(true)
        private set
    /** The latest output is well out of sight (more than 150 dp below the view). */
    internal var far by mutableStateOf(false)

    internal fun sync() { attached = logic.attached }

    /** "Jump to latest": detached and the latest output is out of sight. */
    val showJump: Boolean get() = !attached && far

    /** Scroll to the very end (the last item can be taller than the screen). Only ever moves down. */
    suspend fun toEnd() {
        val info = list.layoutInfo
        val n = info.totalItemsCount
        if (n == 0) return
        // The last item already shows: scroll on from here. Jumping to its top first would move UP
        // when the reader is in the middle of a tall reply.
        if (info.visibleItemsInfo.none { it.index == n - 1 }) list.scrollToItem(n - 1)
        list.scrollBy(100_000f)
    }

    /** Stop following and show item [index] (a memory's "From a chat" opened the chat at it). */
    suspend fun show(index: Int) {
        logic.detach()
        sync()
        list.scrollToItem(index)
    }

    /** Attach and go to the latest output. */
    suspend fun jump() {
        logic.jump()
        sync()
        toEnd()
    }
}

/**
 * Follow [list] while attached: every change of [content] (new text, steps, cards) and every growth
 * of the layout scrolls to the end. Returns the state for the "Jump to latest" pill.
 */
@Composable
fun rememberFollow(list: LazyListState, content: Any?): FollowState {
    val state = remember(list) { FollowState(list) }
    val slack = with(LocalDensity.current) { 48.dp.roundToPx() }
    val jumpDistance = with(LocalDensity.current) { 150.dp.roundToPx() }
    LaunchedEffect(list) {
        var dragFrom: ListPos? = null
        launch {
            list.interactionSource.interactions.collect { i ->
                if (i is DragInteraction.Start) {
                    dragFrom = list.layoutInfo.pos()
                    state.logic.dragStarted()
                }
            }
        }
        launch {
            snapshotFlow { list.isScrollInProgress }.distinctUntilChanged().collect { busy ->
                if (!busy) {
                    val info = list.layoutInfo
                    val from = dragFrom
                    state.logic.scrollSettled(atBottom(info, slack), movedUp = from != null && info.pos() < from)
                    dragFrom = null
                    state.sync()
                }
            }
        }
        // The position: moving up away from the bottom detaches, reaching the bottom attaches.
        launch {
            var lastPos: ListPos? = null
            // Both from one layout pass (firstVisibleItemIndex can run ahead of layoutInfo).
            snapshotFlow { list.layoutInfo.let { atBottom(it, slack) to it.pos() } }
                .distinctUntilChanged().collect { (bottom, pos) ->
                    val was = lastPos
                    lastPos = pos
                    state.logic.moved(up = was != null && pos < was, atBottom = bottom)
                    state.sync()
                }
        }
        launch {
            snapshotFlow { distanceToEnd(list.layoutInfo) > jumpDistance }.distinctUntilChanged().collect { state.far = it }
        }
        // Growth, not position: more items, a taller last item (streamed text, steps, a card,
        // animateContentSize) or a shorter viewport (the keyboard). While attached and not dragged,
        // it scrolls to the very end. Scrolling never changes these sizes, so this cannot loop.
        var last: FollowSizes? = null
        snapshotFlow {
            val info = list.layoutInfo
            val end = info.visibleItemsInfo.lastOrNull()?.takeIf { it.index == info.totalItemsCount - 1 }
            FollowSizes(info.totalItemsCount, end?.size, info.viewportEndOffset - info.viewportStartOffset)
        }.distinctUntilChanged().collect { now ->
            val was = last
            last = now
            // The first layout counts as growth: a chat that opens with a long history shows its end.
            val grew = if (was == null) now.items > 0 else now.items > was.items || now.viewport < was.viewport ||
                (now.lastSize != null && was.lastSize != null && now.lastSize > was.lastSize)
            if (grew && state.logic.shouldFollow(!list.canScrollForward)) state.toEnd()
        }
    }
    LaunchedEffect(content) {
        if (state.logic.attached) state.toEnd()
    }
    return state
}
