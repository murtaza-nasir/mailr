package io.github.murtazanasir.mailr.ui.components

import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue

/**
 * The value of a text field whose text can also change from outside, by dictation. Typing keeps the
 * field's own cursor and keyboard composition. Text that arrives from outside puts the cursor at the
 * end, after the words just added. A field given a plain String keeps its old cursor instead, which
 * left it at the start of a field that was empty.
 */
object DictatedText {
    /** The value to show for [text], given the value the field last reported ([last]). */
    fun value(last: TextFieldValue, text: String): TextFieldValue =
        if (last.text == text) last else TextFieldValue(text, TextRange(text.length))
}

/** [DictatedText] as state: the value to show, and the change handler that reports the text to [onText]. */
@Composable
fun rememberDictatedText(text: String, onText: (String) -> Unit): Pair<TextFieldValue, (TextFieldValue) -> Unit> {
    var last by remember { mutableStateOf(TextFieldValue(text, TextRange(text.length))) }
    val value = DictatedText.value(last, text)
    return value to { v: TextFieldValue -> last = v; onText(v.text) }
}
