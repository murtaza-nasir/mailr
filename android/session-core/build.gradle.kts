// The shared sign-in module: the API client (ApiClient), the device session (SessionStore, sealed with a
// Keystore key), OIDC with PKCE and password sign-in (AuthManager), the offline sign-out retry, the
// server-and-sign-in screen (OnboardingScreen) and the Notes module's transport (NotesTransport). The
// mailr app and the Notes app both use it; each passes its own name, user agent, device label and icon.
plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "io.github.murtazanasir.mailr.session"
    compileSdk = 36

    defaultConfig {
        minSdk = 29
        consumerProguardFiles("consumer-rules.pro")
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        freeCompilerArgs.addAll(
            "-opt-in=androidx.compose.material3.ExperimentalMaterial3Api",
        )
    }
}


dependencies {
    api(project(":notes-core"))
    implementation(libs.core.ktx)
    api(libs.coroutines.android)
    api(libs.serialization.json)
    api(libs.okhttp)
    api(libs.appauth)
    implementation(libs.security.crypto)
    implementation(libs.browser)
    api(libs.work.runtime)

    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.material3)
    implementation(libs.compose.icons.extended)
    implementation(libs.lifecycle.viewmodel.compose)
    implementation(libs.lifecycle.runtime.compose)
    implementation(libs.activity.compose)
}
