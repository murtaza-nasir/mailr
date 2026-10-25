package io.github.murtazanasir.mailr.ui.present

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Follow/detach (as the web's useFollowScroll): attached at the
 * bottom within the slack, detached only by a move up away from the bottom, and the pill only while
 * detached with the latest output out of sight.
 */
class FollowLogicTest {
    @Test fun followsUntilTheUserScrollsUpThenJumpReattaches() {
        val f = FollowLogic()
        assertTrue(f.attached)
        f.moved(up = false, atBottom = false)  // following moved the log down; the reply grew again before the next look
        assertTrue(f.attached)
        f.dragStarted(); f.moved(up = true, atBottom = false)
        assertFalse(f.attached)                // the reader scrolled up
        assertTrue(f.showJump(far = true))
        assertFalse(f.showJump(far = false))   // the latest output is still in sight: no pill
        assertFalse(f.shouldFollow(atBottom = false))  // detached: growth below never scrolls
        f.scrollSettled(atBottom = false, movedUp = true)
        assertFalse(f.attached)                // more output does not snap back
        f.jump()
        assertTrue(f.attached)
        assertFalse(f.showJump(far = true))
    }

    @Test fun aQuickSwipeUpThatSettlesBeforeTheMoveIsSeenStillDetaches() {
        val f = FollowLogic()
        f.dragStarted()
        f.scrollSettled(atBottom = false, movedUp = true)
        assertFalse(f.attached)
    }

    /** A drag down while the reply streams (the log is briefly short of the end) never detaches. */
    @Test fun aDragDownNeverDetaches() {
        val f = FollowLogic()
        f.dragStarted(); f.moved(up = false, atBottom = false)
        f.scrollSettled(atBottom = false, movedUp = false)
        assertTrue(f.attached)
    }

    @Test fun aDragAtTheBottomDoesNotDetachLaterGrowth() {
        val f = FollowLogic()
        f.dragStarted(); f.scrollSettled(atBottom = true)
        f.moved(up = false, atBottom = false)  // the reply grew
        assertTrue(f.attached)
    }

    /** Within the slack counts as the bottom: a small move up there keeps following. */
    @Test fun aSmallMoveUpWithinTheSlackStaysAttached() {
        val f = FollowLogic()
        f.dragStarted(); f.moved(up = true, atBottom = true)
        f.scrollSettled(atBottom = true, movedUp = true)
        assertTrue(f.attached)
    }

    /** At the bottom, the chat once stopped following a reply that grew in place. */
    @Test fun growthBelowWhileAttachedScrollsAgainButNotDuringTheUsersDrag() {
        val f = FollowLogic()
        assertTrue(f.shouldFollow(atBottom = false))    // the reply grew, or the keyboard opened
        assertFalse(f.shouldFollow(atBottom = true))
        f.dragStarted()
        assertFalse(f.shouldFollow(atBottom = false))   // the reader's own scroll is never fought
        f.scrollSettled(atBottom = true)
        assertTrue(f.shouldFollow(atBottom = false))
    }

    /** TalkBack's scroll actions and scrollTo move the list without a drag: up and away from the bottom they detach too. */
    @Test fun aScrollUpByAnythingElseDetaches() {
        val f = FollowLogic()
        f.moved(up = true, atBottom = true)
        assertTrue(f.attached)
        f.moved(up = true, atBottom = false)
        assertFalse(f.attached)
        assertFalse(f.shouldFollow(atBottom = false))
    }

    @Test fun reachingTheBottomByHandReattaches() {
        val f = FollowLogic()
        f.dragStarted(); f.moved(up = true, atBottom = false)
        assertFalse(f.attached)
        f.moved(up = false, atBottom = true)
        assertTrue(f.attached)
    }
}
