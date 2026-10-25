// ACT-SURFACES: android.settings
package io.github.murtazanasir.mailr.ui.memory

import android.app.Application
import android.os.Looper
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.api.MemoryEdit
import io.github.murtazanasir.mailr.data.api.MemorySubject
import io.github.murtazanasir.mailr.testing.eventually
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.io.IOException

/**
 * The Memory page's model: the web's sections (About you, People, Topics, To review), one search and
 * the kind filter over all of them, and memory.accept_suggestion, memory.dismiss_suggestion,
 * memory.add, memory.learn_now, memory.make_task, delete with Undo, and the offline and error states.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class MemoryViewModelTest {
    @get:Rule val graphs = TestGraph()

    private fun idle() = shadowOf(Looper.getMainLooper()).idle()

    /** The next message; the main looper runs first, so a message still on its way is sent. */
    private fun next(vm: MemoryViewModel) = idle().let { runBlocking { withTimeout(2000) { vm.messages.first() } } }

    private fun model(store: FakeMemoryStore = FakeMemoryStore()): Pair<MemoryViewModel, FakeMemoryStore> {
        val vm = graphs.vm { MemoryViewModel(store) }
        eventually("loaded") { !vm.ui.value.loading }
        return vm to store
    }

    @Test fun sectionsFollowTheWeb() {
        val (vm, _) = model()
        val s = MemorySections.of(vm.ui.value)
        assertEquals(listOf(11L, 12L), s.about.map { it.id })
        assertEquals(listOf("Priya Shah", "Theo Martin"), s.people.map { it.title })
        assertEquals(listOf(13L, 14L), s.people[0].items.map { it.id })
        assertEquals(listOf("Lumen Coffee rebrand"), s.topics.map { it.title })
        assertEquals(listOf(21L, 22L), s.review.map { it.id })
    }

    @Test fun oneSearchAndOneKindFilterCoverEverySection() {
        val (vm, _) = model()
        vm.setQuery("priya")
        var s = MemorySections.of(vm.ui.value)
        assertEquals(listOf(13L, 14L), (s.about + s.people.flatMap { it.items } + s.topics.flatMap { it.items }).map { it.id })
        // A person's name matches too, not only the text.
        vm.setQuery("theo martin")
        assertEquals(listOf(15L), MemorySections.of(vm.ui.value).people.flatMap { it.items }.map { it.id })
        vm.setQuery("")
        vm.setKind("preference")
        s = MemorySections.of(vm.ui.value)
        assertEquals(listOf(11L), s.about.map { it.id })
        assertEquals(listOf(15L), s.people.flatMap { it.items }.map { it.id })
        assertEquals(listOf(22L), s.review.map { it.id })
        assertTrue(s.topics.isEmpty())
        // The chosen chip again: All.
        vm.setKind("preference")
        assertEquals(null, vm.ui.value.kind)
        vm.setQuery("nothing like this")
        assertTrue(MemorySections.of(vm.ui.value).isEmpty)
    }

    @Test fun keepDismissAndKeepAll() {
        val (vm, store) = model()
        vm.dismiss(listOf(21))
        eventually("dismissed") { vm.ui.value.suggested.map { it.id } == listOf(22L) }
        vm.keep(listOf(22))
        eventually("kept") { vm.ui.value.suggested.isEmpty() && vm.ui.value.active.any { it.id == 22L } }
        assertEquals(listOf("dismiss:21", "accept:22"), store.calls.filter { it.startsWith("dismiss") || it.startsWith("accept") })
        val msg = next(vm)
        assertEquals("1 memory kept.", msg.text)
    }

    @Test fun addEditAndDeleteWithUndo() {
        val (vm, store) = model()
        vm.save(null, MemoryEdit("Sam is your accountant", "fact", MemorySubject.New("Sam Okafor", "person")))
        eventually("added") { vm.ui.value.active.any { it.text == "Sam is your accountant" } }
        assertEquals("Saved to Ambrose's memory.", next(vm).text)
        vm.save(13, MemoryEdit("Priya runs the Friday design review", "fact", MemorySubject.Known(2)))
        eventually("edited") { vm.ui.value.active.any { it.text == "Priya runs the Friday design review" } }
        assertEquals("Memory saved.", next(vm).text)
        val gone = vm.ui.value.active.first { it.id == 15L }
        vm.delete(gone)
        // Gone at once; the message offers Undo.
        assertFalse(vm.ui.value.active.any { it.id == 15L })
        val msg = next(vm)
        assertEquals("Memory deleted.", msg.text)
        assertEquals(gone, msg.undo)
        vm.undoDelete(msg.undo!!)
        eventually("restored") { vm.ui.value.active.any { it.text == gone.text } }
        assertEquals(listOf(gone), store.restored)
    }

    @Test fun learnNowAndMakeATask() {
        val (vm, store) = model()
        vm.learnNow()
        assertEquals("Learning started. New suggestions show under To review.", next(vm).text)
        vm.makeTask(vm.ui.value.active.first { it.id == 14L })
        assertEquals("Task added.", next(vm).text)
        assertEquals(listOf(14L), store.tasks.map { it.id })
        assertTrue("learn" in store.calls)
    }

    @Test fun aCommitmentBecomesATaskSummary() {
        assertEquals("Promised Priya the brand guide by Friday", taskSummaryOf("You promised Priya the brand guide by Friday."))
        assertEquals("Call the printer", taskSummaryOf("call the printer"))
    }

    @Test fun offlineAndFailedLoads() {
        val (offline, _) = model(FakeMemoryStore(failWith = IOException("no network")))
        assertEquals(LoadProblem.OFFLINE, offline.ui.value.problem)
        val (failed, store) = model(FakeMemoryStore(failWith = IllegalStateException("500")))
        assertEquals(LoadProblem.FAILED, failed.ui.value.problem)
        // Retry reads again and clears the problem.
        store.failWith = null
        failed.load()
        eventually("reloaded") { failed.ui.value.problem == null && failed.ui.value.active.isNotEmpty() }
    }
}
