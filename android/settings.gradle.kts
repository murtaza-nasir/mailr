pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "mailr"
include(":app")
// Notes, checklists and tables: data, sync, offline queue, item grammar and screens, shared by the
// mailr app and the Notes app.
include(":notes-core")
// Sign-in, the device session and the API client, shared by the mailr app and the Notes app.
include(":session-core")
// The separate Notes app: application id io.github.murtazanasir.mailr.notes.
include(":notes")
// The fake notes server for both apps' unit tests.
include(":notes-testing")
