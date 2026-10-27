import java.time.Duration
import java.util.Properties

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
// mounts MAILR_KEYSTORE_DIR read-only for a checkout that has no key of its own). A relative
// storeFile is read beside the properties file.
val keystoreFile: File = System.getenv("MAILR_KEYSTORE_PROPERTIES")?.let(::File) ?: rootProject.file("keystore.properties")
val keystoreProps = Properties().apply {
    if (keystoreFile.exists()) keystoreFile.inputStream().use { load(it) }
}

// Per-deployment settings (buildSrc/MailrDeploy.kt): deploy.properties, each key overridable by an
// environment variable. Unset, the app has no https app links (the mailr:// scheme still works) and
// the server field starts empty.
val appLinkHosts: List<String> = MailrDeploy.appLinkHosts(project)
val defaultServer: String = MailrDeploy.defaultServer(project)

// MainActivity's https app-link filters, one <data> per host and path, as a manifest overlay.
val appLinksManifest = tasks.register<AppLinksManifest>("generateAppLinksManifest") {
    hosts.set(appLinkHosts)
    activity.set(".ui.MainActivity")
    filters.set(listOf(
        "Citation and share links: https://<server>/c/<conversation>?m=<message>&q=<quote>\t/c/,/mail/",
        "Calendar and Tasks app links: https://<server>/calendar/… and /tasks/… (the app checks the host against the signed-in account)\t/calendar,/tasks",
    ))
}

android {
    namespace = "io.github.murtazanasir.mailr"
    compileSdk = 36

    defaultConfig {
        // io.github.murtazanasir.mailr (buildSrc/MailrIds.kt).
        applicationId = MailrIds.MAILR
        minSdk = 29
        targetSdk = 36
        versionCode = 110
        versionName = "0.40.25"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        // AppAuth receives the OIDC redirect on this scheme (io.github.murtazanasir.mailr:/oauth2redirect).
        manifestPlaceholders["appAuthRedirectScheme"] = MailrIds.MAILR
        // The Notes app this app pairs with (AndroidManifest <queries>; session-core Handover).
        manifestPlaceholders["notesApplicationId"] = MailrIds.NOTES
        vectorDrawables.useSupportLibrary = true
        // The app's own languages (res/values-*): the libraries' other ~80 translations stay out of the
        // APK. en-XA and ar-XB are the debug build's pseudo-languages (RtlScreenshotTest).
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
            // The launcher shortcuts name the package (res/xml/shortcuts.xml).
            resValue("string", "shortcut_package", MailrIds.MAILR)
        }
        debug {
            // en-XA and ar-XB, Android's pseudo-languages, for layout testing:
            // the right-to-left screenshots (RtlScreenshotTest) run in ar-XB.
            isPseudoLocalesEnabled = true
            buildConfigField("String", "DEFAULT_SERVER", "\"\"")
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
            resValue("string", "shortcut_package", MailrIds.MAILR + ".debug")
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
                // DocsShotsTest records the docs screenshots and checks nothing: only with -Pmailr.docsShots=true.
                if (!MailrTests.docsShots(project)) it.filter.excludeTestsMatching("io.github.murtazanasir.mailr.DocsShotsTest")
                it.maxHeapSize = "2g"
                // `testDebugUnitTest --tests <class>` runs in both apps; the one without the class has nothing to run.
                it.filter.isFailOnNoMatchingTests = false
                // A hang fails the run instead of holding the build lock for half an hour.
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
            "-opt-in=androidx.compose.material3.adaptive.ExperimentalMaterial3AdaptiveApi",
            "-opt-in=androidx.compose.foundation.ExperimentalFoundationApi",
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
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    implementation(libs.compose.material3.navsuite)
    implementation(libs.compose.icons.extended)
    implementation(libs.adaptive)
    implementation(libs.adaptive.layout)
    implementation(libs.adaptive.navigation)
    implementation(libs.window)
    implementation(libs.activity.compose)
    implementation(libs.lifecycle.runtime.compose)
    implementation(libs.lifecycle.viewmodel.compose)
    implementation(libs.lifecycle.process)
    implementation(libs.navigation.compose)

    implementation(libs.room.runtime)
    implementation(libs.room.ktx)
    implementation(libs.room.paging)
    ksp(libs.room.compiler)
    implementation(libs.paging.runtime)
    implementation(libs.paging.compose)
    implementation(libs.work.runtime)
    implementation(libs.datastore.prefs)

    implementation(libs.okhttp)
    implementation(libs.okhttp.sse)
    implementation(libs.appauth)
    implementation(libs.security.crypto)
    implementation(libs.browser)
    implementation(libs.splashscreen)
    implementation(libs.markdown.m3)
    // Notes while recording (New recording): rich text kept as Markdown.
    implementation(libs.richeditor)
    // The "Ask mailr" home-screen widget.
    implementation(libs.glance.appwidget)
    implementation(libs.glance.material3)
    // Push through the user's UnifiedPush distributor (ntfy or another), data/push.
    // The connector brings Tink for Java; security-crypto already has Tink for Android (same
    // classes), so the connector uses that one, at the connector's Tink version.
    implementation(libs.unifiedpush.connector) { exclude(group = "com.google.crypto.tink", module = "tink") }
    implementation(libs.tink.android)

    debugImplementation(libs.compose.ui.tooling)
    debugImplementation(libs.compose.ui.test.manifest)

    testImplementation(project(":notes-testing"))
    testImplementation(libs.junit)
    testImplementation(libs.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.robolectric)
    testImplementation("androidx.test:core-ktx:1.6.1")
    testImplementation(libs.roborazzi)
    testImplementation(libs.roborazzi.compose)
    testImplementation(libs.roborazzi.junit)
    testImplementation(platform(libs.compose.bom))
    testImplementation(libs.compose.ui.test.junit4)
    testImplementation(libs.paging.testing)
    testImplementation(libs.work.testing)
}
