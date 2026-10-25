// Test support for both apps' unit tests: the fake notes server (MockWebServer), the Compose rule
// (io.github.murtazanasir.mailr.testing.createComposeRule), the shared waits (eventually, settle), VirtualTimeline
// and LooperDump. The fake notes server: the mailr app's Notes tab and the Notes app run against the
// same fake, so neither keeps a copy of its own.
plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "io.github.murtazanasir.mailr.notes.testing"
    compileSdk = 36
    defaultConfig { minSdk = 29 }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}


dependencies {
    api(libs.okhttp.mockwebserver)
    api(libs.serialization.json)
    // The shared Compose rule (io.github.murtazanasir.mailr.testing): the tests that use it bring these.
    compileOnly(libs.coroutines.android)
    compileOnly(platform(libs.compose.bom))
    compileOnly(libs.compose.ui.test.junit4)
    compileOnly(libs.junit)
    // The shared waits (Waits.kt) idle Robolectric's main looper: both apps' tests bring it.
    compileOnly(libs.robolectric)
    // AppClock: the fake server stamps deletions at the app's (pinned) "now". Both apps bring it.
    compileOnly(project(":notes-core"))
}
