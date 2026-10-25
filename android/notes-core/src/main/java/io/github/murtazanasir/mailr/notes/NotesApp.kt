package io.github.murtazanasir.mailr.notes

import io.github.murtazanasir.mailr.util.catching

import android.content.Context

/**
 * The separate Notes app. The mailr app asks whether it
 * is installed: "Open in Notes app" shows then, and note reminders fire from the Notes app only, so a
 * phone with both apps rings once.
 */
object NotesApp {
    /** The Notes app's application id: the mailr app's id and ".notes" (android/buildSrc/MailrIds.kt). */
    const val APPLICATION_ID = "io.github.murtazanasir.mailr.notes"

    /** Its debug build, beside the mailr app's debug build (`.debug`, as app/build.gradle.kts names it). */
    const val DEBUG_APPLICATION_ID = "$APPLICATION_ID.debug"

    /** Whether a Notes app is installed: either build of the published one. */
    fun installed(context: Context): Boolean = listOf(APPLICATION_ID, DEBUG_APPLICATION_ID).any { id ->
        catching { context.packageManager.getPackageInfo(id, 0) }.isSuccess
    }
}
