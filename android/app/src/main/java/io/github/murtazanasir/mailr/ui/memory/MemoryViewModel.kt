package io.github.murtazanasir.mailr.ui.memory

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.LearningStatus
import io.github.murtazanasir.mailr.data.api.Memory
import io.github.murtazanasir.mailr.data.api.MemoryEdit
import io.github.murtazanasir.mailr.i18n.AppText
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.IOException
import java.text.Collator

/** Why the page could not load: no connection, or the server failed. */
enum class LoadProblem { OFFLINE, FAILED }

/** The four kinds, each with its example (the filter chips' descriptions and the add sheet). */
enum class MemoryKind(val id: String, val label: Int, val example: Int) {
    FACT("fact", R.string.memory_kind_fact, R.string.memory_kind_fact_example),
    PREFERENCE("preference", R.string.memory_kind_preference, R.string.memory_kind_preference_example),
    ROUTINE("routine", R.string.memory_kind_routine, R.string.memory_kind_routine_example),
    COMMITMENT("commitment", R.string.memory_kind_commitment, R.string.memory_kind_commitment_example);

    companion object {
        fun of(id: String): MemoryKind? = entries.firstOrNull { it.id == id }
    }
}

data class MemoryUi(
    val loading: Boolean = true,
    val problem: LoadProblem? = null,
    val active: List<Memory> = emptyList(),
    val suggested: List<Memory> = emptyList(),
    val learning: LearningStatus? = null,
    val query: String = "",
    /** A [MemoryKind.id], or null for All. */
    val kind: String? = null,
    /** Suggestions being kept or dismissed. */
    val busy: Set<Long> = emptySet(),
    val learnBusy: Boolean = false,
) {
    val loaded: Boolean get() = !loading || active.isNotEmpty() || suggested.isNotEmpty()
    val filtered: Boolean get() = query.isNotBlank() || kind != null
}

/** One heading of People or Topics: a person or topic and its memories. */
data class MemoryGroup(val entityId: Long, val title: String, val kind: String, val items: List<Memory>)

/** The page's sections after the search and the kind filter, in the web's order. */
data class MemorySections(
    val review: List<Memory>,
    val about: List<Memory>,
    val people: List<MemoryGroup>,
    val topics: List<MemoryGroup>,
) {
    val isEmpty: Boolean get() = review.isEmpty() && about.isEmpty() && people.isEmpty() && topics.isEmpty()

    companion object {
        fun matches(m: Memory, q: String, kind: String?): Boolean {
            val needle = q.trim().lowercase()
            return (kind == null || m.kind == kind) &&
                (needle.isEmpty() || m.text.lowercase().contains(needle) || m.entity?.name.orEmpty().lowercase().contains(needle))
        }

        /** As the web's MemorySection: About you holds memories with no entry or your own; the rest group by entry. */
        fun of(ui: MemoryUi, collator: Collator = Collator.getInstance()): MemorySections {
            val shown = ui.active.filter { matches(it, ui.query, ui.kind) }
            val about = shown.filter { it.aboutYou }
            val groups = shown.filterNot { it.aboutYou }.mapNotNull { m -> m.entity?.let { it to m } }.groupBy({ it.first.id }, { it })
                .map { (id, pairs) -> MemoryGroup(id, pairs.first().first.name, pairs.first().first.kind, pairs.map { it.second }) }
                .sortedWith { a, b -> collator.compare(a.title, b.title) }
            return MemorySections(ui.suggested.filter { matches(it, ui.query, ui.kind) }, about,
                groups.filter { it.kind == "person" }, groups.filter { it.kind != "person" })
        }
    }
}

/** A message for the page's snackbar; [undo] is set for a delete. */
data class MemoryMessage(val text: String, val undo: Memory? = null)

/**
 * The Memory page: learning status, To review, About you, People and
 * Topics, with one search and one kind filter. The same reads and writes as the web's
 * Settings → Ambrose → Memory.
 */
class MemoryViewModel(private val store: MemoryStore) : ViewModel() {
    /** Ambrose's name, for "Saved to Ambrose's memory." */
    var assistantName: String = io.github.murtazanasir.mailr.data.api.AssistantGate.DEFAULT_NAME
    private val _ui = MutableStateFlow(MemoryUi())
    val ui: StateFlow<MemoryUi> = _ui.asStateFlow()
    private val _messages = Channel<MemoryMessage>(Channel.BUFFERED)
    val messages = _messages.receiveAsFlow()

    init { load() }

    fun load() {
        _ui.update { it.copy(loading = true, problem = null) }
        viewModelScope.launch {
            try {
                val active = store.list("active").items
                val suggested = store.list("suggested").items
                _ui.update { it.copy(loading = false, problem = null, active = active, suggested = suggested) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _ui.update { it.copy(loading = false, problem = if (e is IOException) LoadProblem.OFFLINE else LoadProblem.FAILED) }
            }
            // The learning line loads apart: the page shows without it.
            try {
                val l = store.learning()
                _ui.update { it.copy(learning = l) }
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
            }
        }
    }

    fun setQuery(q: String) = _ui.update { it.copy(query = q) }

    /** Tapping the chosen kind again shows All, as on the web. */
    fun setKind(kind: String?) = _ui.update { it.copy(kind = if (kind == it.kind) null else kind) }

    // ACT memory.learn_now @android.settings
    fun learnNow() {
        if (_ui.value.learnBusy) return
        _ui.update { it.copy(learnBusy = true) }
        viewModelScope.launch {
            try {
                store.learn()
                say(AppText.get(R.string.memory_learn_queued))
                // The pass runs in the background; look again shortly, as the web does.
                delay(4000)
                load()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                say(failure(e))
            } finally {
                _ui.update { it.copy(learnBusy = false) }
            }
        }
    }

    // ACT memory.accept_suggestion @android.settings, ACT memory.dismiss_suggestion @android.settings
    fun keep(ids: List<Long>) = review(ids, keep = true)

    fun dismiss(ids: List<Long>) = review(ids, keep = false)

    private fun review(ids: List<Long>, keep: Boolean) {
        if (ids.isEmpty()) return
        _ui.update { it.copy(busy = it.busy + ids) }
        viewModelScope.launch {
            var done = 0
            try {
                for (id in ids) {
                    if (keep) store.accept(id) else store.dismiss(id)
                    done++
                }
                if (keep) say(AppText.plural(R.plurals.memory_kept, done, done))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                say(failure(e))
            } finally {
                _ui.update { it.copy(busy = it.busy - ids.toSet()) }
                load()
            }
        }
    }

    // ACT memory.add @android.settings: a new memory, or an edit of one ([id]).
    fun save(id: Long?, edit: MemoryEdit, done: (Boolean) -> Unit = {}) {
        viewModelScope.launch {
            try {
                store.save(id, edit)
                say(if (id == null) AppText.get(R.string.memory_will_remember, assistantName) else AppText.get(R.string.memory_saved))
                done(true)
                load()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                say(failure(e))
                done(false)
            }
        }
    }

    fun pin(m: Memory) {
        _ui.update { s -> s.copy(active = s.active.map { if (it.id == m.id) it.copy(pinned = !m.pinned) else it }) }
        viewModelScope.launch {
            try {
                store.pin(m.id, !m.pinned)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                say(failure(e))
                load()
            }
        }
    }

    /** Delete at once; the snackbar offers Undo. */
    fun delete(m: Memory) {
        _ui.update { s -> s.copy(active = s.active.filter { it.id != m.id }) }
        viewModelScope.launch {
            try {
                store.delete(m.id)
                _messages.send(MemoryMessage(AppText.get(R.string.memory_deleted), undo = m))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                say(failure(e))
                load()
            }
        }
    }

    fun undoDelete(m: Memory) {
        viewModelScope.launch {
            try {
                store.restore(m)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                say(failure(e))
            }
            load()
        }
    }

    fun makeTask(m: Memory) {
        viewModelScope.launch {
            try {
                store.makeTask(m)
                say(AppText.get(R.string.memory_task_made))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                say(failure(e))
            }
        }
    }

    private suspend fun say(text: String) = _messages.send(MemoryMessage(text))

    private fun failure(e: Exception) =
        if (e is IOException) AppText.get(R.string.memory_not_saved_offline) else AppText.get(R.string.memory_not_saved)
}
