import java.time.Duration
import java.util.Properties

// The Notes app: a Keep-style app for notes, lists and
// tables on the shared modules. Its own application id (io.github.murtazanasir.mailr.notes), launcher name
// "Notes", Room database, device session and OIDC redirect; the same screens, sync and replay queue
// as the mailr app's Notes tab. Its version is its own (1.0.0 first).
plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
    alias(libs.plugins.roborazzi)
}

// A screenshot comparison needs the references in screenshots/ (buildSrc/MailrTests.kt).
MailrTests.requireReferences(project)

// keystore.properties beside this project, or the file MAILR_KEYSTORE_PROPERTIES names (build.sh
// mounts MAILR_KEYSTORE_DIR read-only), as in the mailr app. A relative storeFile is read beside it.
val keystoreFile: File = System.getenv("MAILR_KEYSTORE_PROPERTIES")?.let(::File) ?: rootProject.file("keystore.properties")
val keystoreProps = Properties().apply {
    if (keystoreFile.exists()) keystoreFile.inputStream().use { load(it) }
}

// The same deploy.properties as the mailr app (buildSrc/MailrDeploy.kt): the hosts whose note and
// table links open here, and the server field's starting value.
val appLinkHosts: List<String> = MailrDeploy.appLinkHosts(project)
val defaultServer: String = MailrDeploy.defaultServer(project)

val appLinksManifest = tasks.register<AppLinksManifest>("generateNotesAppLinksManifest") {
    hosts.set(appLinkHosts)
    activity.set(".MainActivity")
    filters.set(listOf(
        "Note and table links: https://<server>/notes/n/<id> and /tables/t/<id>[/r/<row>] (the app checks the host against the signed-in account)\t/notes/n/,/tables/t/",
    ))
}

android {
    namespace = "io.github.murtazanasir.mailr.notesapp"
    compileSdk = 36

    defaultConfig {
        // io.github.murtazanasir.mailr.notes (buildSrc/MailrIds.kt).
        applicationId = MailrIds.NOTES
        minSdk = 29
        targetSdk = 36
        versionCode = 30
        versionName = "2.1.3"
        // AppAuth receives the OIDC redirect on this scheme (io.github.murtazanasir.mailr.notes:/oauth2redirect),
        // never the mailr app's, so the two apps never answer each other's sign-in.
        manifestPlaceholders["appAuthRedirectScheme"] = MailrIds.NOTES
        // The mailr app's release id, for <queries> (both of its builds are listed).
        manifestPlaceholders["mailrPackage"] = MailrIds.MAILR
        vectorDrawables.useSupportLibrary = true
        // The app's own languages only (as the mailr app); en-XA and ar-XB are the debug pseudo-languages.
        resourceConfigurations += listOf("en", "de", "es", "fr", "pt-rBR", "ru", "b+zh+Hans", "en-rXA", "ar-rXB")
    }

    signingConfigs {
        if (keystoreProps.getProperty("storeFile") != null) {
            create("release") {
                storeFile = keystoreFile.parentFile.resolve(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            // The first-run server field (deploy.properties defaultServer); release builds only, so
            // debug builds and their unit tests never reach a real server.
            buildConfigField("String", "DEFAULT_SERVER", "\"$defaultServer\"")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release")
            // The static launcher shortcuts name the package (res/xml/shortcuts.xml).
            resValue("string", "shortcut_package", MailrIds.NOTES)
            // The mailr app this build pairs with for "Continue as" (session-core Handover).
            manifestPlaceholders["mailrApplicationId"] = MailrIds.MAILR
        }
        debug {
            // en-XA and ar-XB, Android's pseudo-languages, for layout testing:
            // the right-to-left screenshots (RtlScreenshotTest) run in ar-XB.
            isPseudoLocalesEnabled = true
            buildConfigField("String", "DEFAULT_SERVER", "\"\"")
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
            resValue("string", "shortcut_package", MailrIds.NOTES + ".debug")
            manifestPlaceholders["mailrApplicationId"] = MailrIds.MAILR + ".debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            all {
                it.systemProperty("robolectric.graphicsMode", "NATIVE")
                // Screenshot references at half size (buildSrc/MailrTests.kt); the docs shots stay full size.
                it.systemProperty("roborazzi.record.resizeScale", MailrTests.shotScale(project))
                // Every test reads "now" at the docs demo's pinned time (Fri 2 Oct 2026, 10:40 in
                // America/Chicago, as the docs screenshots), so screenshots do not drift
                // with the wall clock (AppClock in notes-core).
                it.systemProperty("mailr.clock.now", "2026-10-02T10:40:00-05:00")
                it.systemProperty("user.timezone", "America/Chicago")
                // Robolectric's Android jars from Google's Maven Central mirror (Maven Central rate-limits
                // repeated downloads; MAILR_ROBOLECTRIC_REPO overrides).
                it.systemProperty("robolectric.dependency.repo.url", System.getenv("MAILR_ROBOLECTRIC_REPO")
                    ?: "https://maven-central.storage-download.googleapis.com/maven2")
                // Several test JVMs at once (one ran the whole suite on one core); buildSrc/MailrTests.kt
                // has the defaults and the -P/environment overrides.
                it.maxParallelForks = MailrTests.forks(project)
                it.forkEvery = MailrTests.forkEvery(project)
                it.maxHeapSize = "2g"
                // `testDebugUnitTest --tests <class>` runs in both apps; the one without the class has nothing to run.
                it.filter.isFailOnNoMatchingTests = false
                it.timeout.set(Duration.ofMinutes(20))
            }
        }
    }
    lint {
        // The gate's lint run: errors fail it; warnings stay
        // in build/reports/lint-results-debug.html.
        abortOnError = true
        // Test fixtures build state in composition on purpose.
        ignoreTestSources = true
        // ImpliedQuantity: the plurals use the app's own {{count}} placeholder (AppText), or are unit
        // words shown beside a number formatted apart ("jour", "dia"); lint only knows %d.
        // ProduceStateDoesNotAssignValue: a false positive with Kotlin 2.1 for producers that assign
        // inside an if or after a suspend call (every reported one assigns value).
        disable += setOf("ImpliedQuantity", "ProduceStateDoesNotAssignValue")
    }
    packaging {
        resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}", "/META-INF/versions/9/previous-compilation-data.bin", "DebugProbesKt.bin")
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        freeCompilerArgs.addAll(
            "-opt-in=androidx.compose.material3.ExperimentalMaterial3Api",
            "-opt-in=androidx.compose.foundation.layout.ExperimentalLayoutApi",
            "-opt-in=kotlinx.coroutines.ExperimentalCoroutinesApi",
        )
    }
}


androidComponents {
    onVariants { variant ->
        variant.sources.manifests.addGeneratedManifestFile(appLinksManifest, AppLinksManifest::manifest)
    }
}

ksp {
    arg("room.schemaLocation", "$projectDir/schemas")
}

dependencies {
    implementation(project(":notes-core"))
    implementation(project(":session-core"))
    implementation(libs.core.ktx)
    implementation(libs.coroutines.android)
    implementation(libs.serialization.json)

    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.graphics)
    implementation(libs.compose.material3)
    implementation(libs.compose.icons.extended)
    implementation(libs.activity.compose)
    implementation(libs.lifecycle.runtime.compose)
    implementation(libs.lifecycle.viewmodel.compose)
    implementation(libs.lifecycle.process)

    implementation(libs.room.runtime)
    implementation(libs.room.ktx)
    ksp(libs.room.compiler)
    implementation(libs.work.runtime)
    implementation(libs.okhttp)
    implementation(libs.okhttp.sse)
    implementation(libs.splashscreen)
    implementation(libs.glance.appwidget)
    implementation(libs.glance.material3)

    debugImplementation(libs.compose.ui.tooling)
    debugImplementation(libs.compose.ui.test.manifest)

    testImplementation(project(":notes-testing"))
    testImplementation(libs.junit)
    testImplementation(libs.coroutines.test)
    testImplementation(libs.robolectric)
    testImplementation("androidx.test:core-ktx:1.6.1")
    testImplementation(libs.roborazzi)
    testImplementation(libs.roborazzi.compose)
    testImplementation(libs.roborazzi.junit)
    testImplementation(platform(libs.compose.bom))
    testImplementation(libs.compose.ui.test.junit4)
    testImplementation(libs.work.testing)
    testImplementation(libs.glance.testing)
}
