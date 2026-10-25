package io.github.murtazanasir.mailr.ui.memory

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Close
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import io.github.murtazanasir.mailr.look.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
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
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.EntityDetail
import io.github.murtazanasir.mailr.data.api.KnownEntity
import io.github.murtazanasir.mailr.data.api.Memory
import io.github.murtazanasir.mailr.data.api.MemorySubject
import io.github.murtazanasir.mailr.data.api.PersonCard
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.components.Avatar
import io.github.murtazanasir.mailr.ui.util.Format
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import java.io.IOException
import java.time.ZonedDateTime

/** Whose card: a contact by address (mail, a person lookup) or a person Ambrose knows by entry. */
sealed interface PersonTarget {
    data class Email(val email: String) : PersonTarget
    data class Entry(val id: Long) : PersonTarget
}

sealed interface PersonCardState {
    data object Loading : PersonCardState
    data class Failed(val offline: Boolean) : PersonCardState
    /** A contact: details from mail and the address book, and Ambrose's memory. */
    data class Contact(val card: PersonCard) : PersonCardState
    /** A person (or topic) Ambrose knows with no contact behind it: "No mail". */
    data class Known(val detail: EntityDetail) : PersonCardState
}

/**
 * The card's data as the web's PersonCardDialog reads it: an address opens GET /people/{email}; an
 * entry opens its contact when one carries its address, else the entry itself.
 */
suspend fun loadPersonCard(store: MemoryStore, target: PersonTarget): PersonCardState = when (target) {
    is PersonTarget.Email -> PersonCardState.Contact(store.person(target.email))
    is PersonTarget.Entry -> {
        val d = store.entity(target.id)
        val email = d.contacts.firstOrNull()?.email
        if (email == null) PersonCardState.Known(d)
        else try {
            PersonCardState.Contact(store.person(email))
        } catch (e: ApiException) {
            if (e.code == 404) PersonCardState.Known(d) else throw e
        }
    }
}

/**
 * The person card: what the app knows about the contact (addresses,
 * phones, groups, VIP, mail with them) and "Ambrose's memory", editable. A bottom sheet on phones
 * ([PersonCardSheet]); the Memory page shows it as a pane on wide screens.
 * ACT person.memories @android.settings @android.reader @android.assistant
 */
@Composable
fun PersonCardHost(
    target: PersonTarget,
    store: MemoryStore,
    assistantName: String,
    vip: (String) -> Boolean,
    onClose: () -> Unit,
    openSource: ((Long) -> Unit)? = null,
    /** Called after a memory changed, so the page behind can read again. */
    onChanged: () -> Unit = {},
    now: ZonedDateTime = AppClock.now(),
    /** "From a chat" on a memory: that chat in Ambrose (the Memory page passes it). */
    openChat: ((String, Long?) -> Unit)? = null,
) {
    var state by remember(target) { mutableStateOf<PersonCardState>(PersonCardState.Loading) }
    var reload by remember { mutableIntStateOf(0) }
    var editing by remember { mutableStateOf<MemorySheetRequest?>(null) }
    var entities by remember { mutableStateOf<List<KnownEntity>>(emptyList()) }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    LaunchedEffect(target, reload) {
        state = try {
            loadPersonCard(store, target)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (state is PersonCardState.Loading || reload == 0) PersonCardState.Failed(e is IOException) else state
        }
    }
    fun run(block: suspend () -> Unit, done: String? = null, after: suspend () -> Unit = {}) = scope.launch {
        try {
            block()
            onChanged()
            reload++
            if (done != null) snackbar.showSnackbar(done)
            after()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            snackbar.showSnackbar(AppText.get(if (e is IOException) R.string.memory_not_saved_offline else R.string.memory_not_saved))
        }
    }
    val actions = MemoryCallbacks(
        back = onClose, retry = { reload++ }, setQuery = {}, setKind = {}, learnNow = {}, keep = {}, dismiss = {},
        add = {}, edit = { m -> editing = MemorySheetRequest(m, fixedFor(state), nameOf(state)) },
        delete = { m ->
            state = withoutMemory(state, m.id)
            scope.launch {
                try {
                    store.delete(m.id)
                    onChanged()
                    val r = snackbar.showSnackbar(AppText.get(R.string.memory_deleted), AppText.get(R.string.memory_undo))
                    if (r == SnackbarResult.ActionPerformed) run({ store.restore(m) })
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    reload++
                    snackbar.showSnackbar(AppText.get(if (e is IOException) R.string.memory_not_saved_offline else R.string.memory_not_saved))
                }
            }
        },
        pin = { m -> run({ store.pin(m.id, !m.pinned) }) },
        makeTask = { m -> run({ store.makeTask(m) }, AppText.get(R.string.memory_task_made)) },
        openCard = {}, openSource = openSource, openChat = openChat,
    )
    Box(Modifier.fillMaxWidth()) {
        PersonCardContent(state, assistantName, vip, now, actions,
            onAdd = {
                editing = MemorySheetRequest(null, fixedFor(state), nameOf(state))
            }, onClose = onClose)
        SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter))
    }
    editing?.let { req ->
        LaunchedEffect(Unit) {
            entities = try { store.entities() } catch (e: CancellationException) { throw e } catch (_: Exception) { emptyList() }
        }
        MemoryEditSheet(req, entities, assistantName, onDismiss = { editing = null }) { id, edit ->
            editing = null
            run({ store.save(id, edit) },
                if (id == null) AppText.get(R.string.memory_will_remember, assistantName) else AppText.get(R.string.memory_saved))
        }
    }
}

/** A new memory on this card is about this person: their entry, or a new one with their address. */
private fun fixedFor(s: PersonCardState): MemorySubject? = when (s) {
    is PersonCardState.Contact -> s.card.knowledge.entity?.let { MemorySubject.Known(it.id) }
        ?: MemorySubject.New(s.card.name.ifBlank { s.card.email.substringBefore('@') }, "person", s.card.email)
    is PersonCardState.Known -> MemorySubject.Known(s.detail.id)
    else -> null
}

private fun nameOf(s: PersonCardState): String? = when (s) {
    is PersonCardState.Contact -> s.card.name.ifBlank { s.card.email }
    is PersonCardState.Known -> s.detail.name
    else -> null
}

private fun withoutMemory(s: PersonCardState, id: Long): PersonCardState = when (s) {
    is PersonCardState.Contact -> s.copy(card = s.card.copy(knowledge = s.card.knowledge.copy(memories = s.card.knowledge.memories.filter { it.id != id })))
    is PersonCardState.Known -> s.copy(detail = s.detail.copy(memories = s.detail.memories.filter { it.id != id }))
    else -> s
}

/** The card as a bottom sheet (phones, and the reader and the chat on every screen). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PersonCardSheet(
    target: PersonTarget,
    store: MemoryStore,
    assistantName: String,
    vip: (String) -> Boolean,
    onClose: () -> Unit,
    openSource: ((Long) -> Unit)? = null,
    onChanged: () -> Unit = {},
    openChat: ((String, Long?) -> Unit)? = null,
) {
    ModalBottomSheet(onDismissRequest = onClose, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Box(Modifier.navigationBarsPadding().testTag("person-sheet")) {
            PersonCardHost(target, store, assistantName, vip, onClose, openSource, onChanged, openChat = openChat)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun PersonCardContent(
    state: PersonCardState,
    assistantName: String,
    vip: (String) -> Boolean,
    now: ZonedDateTime,
    cb: MemoryCallbacks,
    onAdd: () -> Unit,
    onClose: () -> Unit,
) {
    Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(bottom = 24.dp).testTag("person-card")) {
        Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(stringResource(R.string.person_card_title), style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
            IconButton(onClick = onClose) { Icon(Icons.Outlined.Close, stringResource(R.string.person_close)) }
        }
        when (state) {
            PersonCardState.Loading -> Box(Modifier.fillMaxWidth().heightIn(min = 200.dp), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            is PersonCardState.Failed -> Column(Modifier.fillMaxWidth().padding(20.dp)) {
                Text(stringResource(if (state.offline) R.string.memory_offline_title else R.string.person_load_failed),
                    color = if (state.offline) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.error)
                TextButton(onClick = cb.retry) { Text(stringResource(R.string.memory_retry)) }
            }
            is PersonCardState.Contact -> {
                val p = state.card
                Header(p.name.ifBlank { p.email }, p.email) {
                    if (vip(p.email)) Pill(stringResource(R.string.person_vip), info = true)
                }
                if (p.title.isNotBlank()) Detail(stringResource(R.string.person_job_title), p.title)
                if (p.organization.isNotBlank()) Detail(stringResource(R.string.person_organisation), p.organization)
                p.phones.forEach { ph ->
                    Detail(stringResource(if (ph.type == "mobile" || ph.type == "cell") R.string.person_mobile else R.string.person_phone), ph.value)
                }
                p.addresses.forEach { a -> Detail(stringResource(R.string.person_address), a.text) }
                if (p.birthday.isNotBlank()) Detail(stringResource(R.string.person_birthday), p.birthday.removePrefix("--"))
                val last = Format.parse(p.lastSeen)?.let { Format.relative(it, now) }
                Detail(stringResource(R.string.person_mail), if (last != null) stringResource(R.string.person_mail_last, p.sentCount, p.receivedCount, last)
                    else stringResource(R.string.person_mail_counts, p.sentCount, p.receivedCount))
                if (p.groups.isNotEmpty()) Detail(stringResource(R.string.person_groups), p.groups.joinToString { it.name })
                Knows(assistantName, p.knowledge.memories, now, cb, onAdd)
            }
            is PersonCardState.Known -> {
                val d = state.detail
                Header(d.name, d.emails.firstOrNull().orEmpty()) {
                    if (d.kind == "person") Pill(stringResource(R.string.person_no_mail)) else Pill(entityKindLabel(d.kind))
                }
                if (d.summary.isNotBlank()) Text(d.summary, style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp))
                if (d.emails.size > 1) Detail(stringResource(R.string.person_emails), d.emails.joinToString())
                if (d.aliases.isNotEmpty()) Detail(stringResource(R.string.person_also_called), d.aliases.joinToString())
                if (d.domains.isNotEmpty()) Detail(stringResource(R.string.person_domains), d.domains.joinToString())
                Knows(assistantName, d.memories, now, cb, onAdd)
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Header(name: String, email: String, pills: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Avatar(name, email.ifBlank { name }, size = 48.dp)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(name, style = MaterialTheme.typography.titleLarge, maxLines = 2, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.semantics { heading() })
            if (email.isNotBlank() && email != name) Text(email, style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.padding(top = 4.dp)) { pills() }
        }
    }
}

@Composable
private fun Detail(label: String, value: String) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 6.dp)) {
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.bodyMedium)
    }
}

/** "Ambrose's memory": the person's memories, editable, with Add memory. */
@Composable
private fun Knows(assistantName: String, memories: List<Memory>, now: ZonedDateTime, cb: MemoryCallbacks, onAdd: () -> Unit) {
    HorizontalDivider(Modifier.padding(horizontal = 20.dp, vertical = 8.dp))
    Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp).testTag("person-knows"), verticalAlignment = Alignment.CenterVertically) {
        Text(stringResource(R.string.person_knows, assistantName), style = MaterialTheme.typography.titleSmall,
            color = MaterialTheme.colorScheme.primary, modifier = Modifier.weight(1f).semantics { heading() })
        TextButton(onClick = onAdd, modifier = Modifier.testTag("person-add-memory")) {
            Icon(Icons.Outlined.Add, null, Modifier.size(18.dp))
            Spacer(Modifier.width(6.dp))
            Text(stringResource(R.string.memory_add))
        }
    }
    if (memories.isEmpty()) Text(stringResource(R.string.person_knows_nothing), color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp))
    memories.forEach { m -> MemoryRow(m, assistantName, now, cb) }
}
