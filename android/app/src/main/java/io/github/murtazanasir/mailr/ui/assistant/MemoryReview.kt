package io.github.murtazanasir.mailr.ui.assistant

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowRight
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.runtime.key
import androidx.compose.ui.text.style.TextOverflow
import io.github.murtazanasir.mailr.ui.memory.SuggestionChanges
import io.github.murtazanasir.mailr.ui.memory.suggestionTopic
import io.github.murtazanasir.mailr.ui.memory.suggestionTopicKey
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.Memory
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import io.github.murtazanasir.mailr.look.FilledTonalButton
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.ui.memory.KindPill
import io.github.murtazanasir.mailr.ui.memory.MemoryStore
import io.github.murtazanasir.mailr.ui.memory.Pill
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/** One "Remember …?" item under a reply: a suggestion from the message before it, and the answer
 *  (waiting, kept, dropped, merged), as the chat's transcript gives it. */
data class RememberItem(val id: Long, val text: String, val state: String)

/**
 * "Remember: …?" with Yes and No under the reply (docs/design/memory-quality.md, C8); after an
 * answer, the line says what became of it.
 * ACT memory.chat_ask @android.assistant
 */
@Composable
fun RememberAsk(items: List<RememberItem>, answer: (Long, Boolean) -> Unit) {
    Column(Modifier.fillMaxWidth().testTag("remember-ask"), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        items.forEach { item ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.Psychology, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                if (item.state == "waiting") {
                    Text(stringResource(R.string.asst_remember_question, item.text), Modifier.weight(1f).padding(start = 6.dp),
                        style = MaterialTheme.typography.bodyMedium)
                    TextButton(onClick = { answer(item.id, true) }) { Text(stringResource(R.string.asst_remember_yes)) }
                    TextButton(onClick = { answer(item.id, false) }) { Text(stringResource(R.string.asst_remember_no)) }
                } else {
                    val res = when (item.state) {
                        "kept" -> R.string.asst_remember_kept
                        "merged" -> R.string.asst_remember_merged
                        else -> R.string.asst_remember_dropped
                    }
                    Text(stringResource(res, item.text), Modifier.padding(start = 6.dp), style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

/** "3 to review" in the top bar: opens the Memory page. ACT memory.review_count @android.assistant */
@Composable
fun ReviewCountChip(count: Int, open: () -> Unit) {
    if (count <= 0) return
    TextButton(onClick = open, modifier = Modifier.testTag("review-count")) {
        Text(pluralStringResource(R.plurals.memory_review_count, count, count))
    }
}

/**
 * The weekly review card at the top of the chat (C9): from Monday morning, while suggestions wait
 * and until dismissed that week. One line per topic with its count, the largest first; one topic
 * opens at a time with Keep, Merge into another note, Dismiss, Create task (a commitment), and
 * Keep all / Dismiss all. The list has a height limit, and [open] goes to the Memory page.
 * [refresh] changes reload it (a reply ended, a suggestion was answered elsewhere); [changed] tells
 * the screen the count moved.
 * ACT memory.accept_suggestion @android.assistant, ACT memory.dismiss_suggestion @android.assistant
 * ACT memory.make_task @android.assistant
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun MemoryReviewCard(store: MemoryStore, refresh: Int, open: (() -> Unit)? = null, changed: (Int) -> Unit) {
    val scope = rememberCoroutineScope()
    var due by remember { mutableStateOf(false) }
    var sugg by remember { mutableStateOf<List<Memory>>(emptyList()) }
    var saved by remember { mutableStateOf<List<Memory>>(emptyList()) }
    var reload by remember { mutableIntStateOf(0) }
    var busy by remember { mutableStateOf(false) }
    var opened by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(refresh, reload) {
        try {
            val c = store.counts()
            changed(c.suggested)
            due = c.reviewCard
            if (due) {
                sugg = store.list("suggested").items
                saved = store.list("active").items
            }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            due = false  // offline: no card
        }
    }
    if (!due || sugg.isEmpty()) return
    val you = stringResource(R.string.memory_you)
    val other = stringResource(R.string.memory_review_other)
    fun act(block: suspend () -> Unit) {
        if (busy) return
        busy = true
        scope.launch {
            try { block() } catch (e: CancellationException) { throw e } catch (_: Exception) { }
            busy = false
            reload++
        }
    }
    val groups = sugg.groupBy { suggestionTopicKey(it, you, other) }.entries.sortedByDescending { it.value.size }
    Card(Modifier.widthIn(max = 760.dp).fillMaxWidth().testTag("review-card"),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer)) {
        Column(Modifier.padding(start = 16.dp, end = 4.dp, top = 4.dp, bottom = 4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(pluralStringResource(R.plurals.memory_review_title, sugg.size, sugg.size), Modifier.weight(1f),
                    style = MaterialTheme.typography.titleSmall)
                IconButton(onClick = { act { store.dismissReviewCard() } }, modifier = Modifier.testTag("review-card-dismiss")) {
                    Icon(Icons.Outlined.Close, stringResource(R.string.memory_review_not_now))
                }
            }
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()).padding(end = 12.dp)) {
                groups.forEach { (key, ms) ->
                    key(key) {
                        val isOpen = opened == key
                        val topic = suggestionTopic(ms.first(), you, other)
                        Row(Modifier.fillMaxWidth().heightIn(min = 44.dp).clickable { opened = if (isOpen) null else key }
                            .testTag("review-topic"), verticalAlignment = Alignment.CenterVertically) {
                            Icon(if (isOpen) Icons.Outlined.ExpandMore else Icons.AutoMirrored.Outlined.KeyboardArrowRight, null,
                                Modifier.size(20.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                            Text(topic, Modifier.weight(1f).padding(start = 6.dp), maxLines = 1, overflow = TextOverflow.Ellipsis,
                                style = MaterialTheme.typography.bodyMedium)
                            Pill(ms.size.toString())
                        }
                        if (isOpen) TopicItems(ms, saved.filter { suggestionTopicKey(it, you, other) == key }, busy, store, ::act)
                    }
                }
            }
            if (open != null) TextButton(onClick = open) { Text(stringResource(R.string.memory_review_open_all)) }
        }
    }
}

/** One opened topic of the review card: Keep all / Dismiss all, then each suggestion with its actions. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TopicItems(ms: List<Memory>, saved: List<Memory>, busy: Boolean, store: MemoryStore, act: (suspend () -> Unit) -> Unit) {
    Column(Modifier.padding(start = 26.dp, bottom = 8.dp).testTag("review-group")) {
        if (ms.size > 1) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                TextButton(onClick = { act { ms.forEach { store.accept(it.id) } } }) { Text(stringResource(R.string.memory_keep_all)) }
                TextButton(onClick = { act { ms.forEach { store.dismiss(it.id) } } }) { Text(stringResource(R.string.memory_dismiss_all)) }
            }
        }
        val targets = saved + ms.filter { it.retires == null }
        ms.forEach { m ->
            key(m.id) {
                Column(Modifier.padding(top = 6.dp).testTag("review-item")) {
                    Text(m.text, style = MaterialTheme.typography.bodyMedium)
                    SuggestionChanges(m)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalArrangement = Arrangement.Center) {
                        KindPill(m.kind)
                        FilledTonalButton(onClick = { act { store.accept(m.id) } }, enabled = !busy) {
                            Text(stringResource(if (m.retires != null) R.string.memory_review_retire else R.string.memory_keep))
                        }
                        val others = if (m.retires != null) emptyList() else targets.filter { it.id != m.id }
                        if (others.isNotEmpty()) {
                            var menu by remember { mutableStateOf(false) }
                            Column {
                                TextButton(onClick = { menu = true }, enabled = !busy, modifier = Modifier.testTag("review-merge-${m.id}")) {
                                    Text(stringResource(R.string.memory_review_merge))
                                }
                                DropdownMenu(menu, { menu = false }) {
                                    others.forEach { t ->
                                        DropdownMenuItem({ Text(t.text, maxLines = 2) }, { menu = false; act { store.merge(m.id, t.id) } })
                                    }
                                }
                            }
                        }
                        if (m.kind == "commitment" && m.retires == null) TextButton(onClick = { act { store.makeTask(m); store.dismiss(m.id) } }, enabled = !busy) {
                            Text(stringResource(R.string.memory_make_task))
                        }
                        TextButton(onClick = { act { store.dismiss(m.id) } }, enabled = !busy) {
                            Text(stringResource(if (m.retires != null) R.string.memory_review_keep_note else R.string.memory_dismiss))
                        }
                    }
                }
            }
        }
    }
}
