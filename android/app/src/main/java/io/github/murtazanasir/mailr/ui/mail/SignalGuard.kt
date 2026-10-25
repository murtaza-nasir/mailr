package io.github.murtazanasir.mailr.ui.mail

import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.Signal

/**
 * Spam and Trash show no triage signals. The server leaves them out of every response
 * (backend core/spamtrash.py); this guard covers rows the app still holds from before a move, so a
 * conversation that is all in Spam or Trash never shows Reply needed, Waiting or Due. Moved back
 * out, it shows its chips again (the decisions stay stored). Before the labels load it does nothing.
 */
internal fun inSpamOrTrash(labelIds: List<Long>, labels: Map<Long, Label>): Boolean {
    if (labelIds.isEmpty()) return false
    val hidden = labels.values.filter { it.role == "junk" || it.role == "trash" }.map { it.id }.toSet()
    return hidden.isNotEmpty() && labelIds.all { it in hidden }
}

internal fun shownSignals(c: ConversationSummary, labels: Map<Long, Label>): List<Signal> =
    if (inSpamOrTrash(c.labelIds, labels)) emptyList() else c.signals
