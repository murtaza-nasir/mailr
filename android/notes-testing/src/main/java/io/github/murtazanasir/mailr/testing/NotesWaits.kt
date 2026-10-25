package io.github.murtazanasir.mailr.testing

import androidx.compose.ui.test.junit4.ComposeContentTestRule
import io.github.murtazanasir.mailr.notes.data.NotesRepository
import kotlinx.coroutines.runBlocking

/**
 * Sends the Notes queue until the fake server shows [check]: an action's write is queued from a
 * ViewModel's scope, so each look first idles the screen and then drains [repo].
 */
fun ComposeContentTestRule.serverSees(repo: NotesRepository, what: String, check: () -> Boolean) =
    eventually("the server saw: $what", idle = { waitForIdle(); runBlocking { repo.drain() } }, check = check)
