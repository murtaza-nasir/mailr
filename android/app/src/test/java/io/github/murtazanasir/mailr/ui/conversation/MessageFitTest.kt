package io.github.murtazanasir.mailr.ui.conversation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * HTML mail on a phone (a bank's mail cut off at the right, a
 * newsletter cut off on both sides). A body wider than the screen loads again with the CSS zoom
 * that fits it, as Gmail shows it; responsive mail stays at zoom 1. Robolectric cannot lay out a
 * WebView, so the widths below are the ones Chromium measures for these bodies at 380 px
 * (fixed 600 px table: 606 px wide; a 600 px block centred in a flex row: 490 px of scroll width,
 * 110 px hidden on the left).
 */
class MessageFitTest {
    /** A typical fixed-width newsletter: one 600 px table. */
    private val fixed600 = """<table width="600" cellpadding="0" cellspacing="0" style="width:600px"><tr>
        |<td width="600" style="width:600px;padding:24px;background:#e8f0fe;font:16px Arial">
        |<h1 style="margin:0">Your statement is ready</h1><p>Balance <b>${'$'}1,234.56</b>, due <b>Oct 28</b>.</p>
        |<table width="552"><tr><td width="276">Left column</td><td width="276" align="right">Right column END</td></tr></table>
        |</td></tr></table>""".trimMargin()

    @Test fun aFixed600pxTableFitsTheScreenWidth() {
        val z = fitZoom(viewPx = 380, contentPx = 606)!!
        assertEquals(0.627f, z, 0.0001f)
        assertTrue("fits", 606 * z <= 380)
        val doc = wrap(fixed600, showQuote = false, darkText = true, allowImages = false, zoom = z)
        assertTrue(doc.contains("html{zoom:0.627}"))
        // No horizontal page offset: nothing in the page asks for one, and the body is not shifted.
        assertFalse(doc.contains("margin-left:-"))
        // For a look in a browser at phone width (Chromium): build/fit/fixed600.html
        File("build/fit").mkdirs()
        File("build/fit/fixed600.html").writeText(doc)
        File("build/fit/fixed600-unfitted.html").writeText(wrap(fixed600, false, true))
    }

    @Test fun aBodyThatFitsKeepsZoomOne() {
        assertNull(fitZoom(380, 380))
        assertNull(fitZoom(380, 382))   // rounding slack
        assertNull(fitZoom(0, 600))     // not laid out yet
        assertFalse(wrap("<p>x</p>", false, true).contains("zoom"))
    }

    /** The Google mail: a block centred in a flex row is cut on both sides; the second pass counts the hidden left half. */
    @Test fun aCentredOverflowFitsOnTheSecondPass() {
        val first = fitZoom(380, 490)!!
        assertEquals(0.775f, first, 0.0001f)
        // At 0.775 Chromium measures 423 px of scroll width (42 px still hidden on the left).
        val second = fitZoom(380, 423, first)!!
        assertEquals("600 px fits", 0.631f, second, 0.0005f)
        assertTrue(600 * second <= 380)
    }

    @Test fun neverZoomsInAndNeverBelowAQuarter() {
        assertTrue(fitZoom(380, 385, current = 0.99f)!! < 0.99f)
        assertEquals(0.25f, fitZoom(380, 5000)!!, 0f)
        assertNull(fitZoom(380, 5000, current = 0.25f))
    }
}
