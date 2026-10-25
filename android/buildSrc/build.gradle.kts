// Build logic shared by the mailr app and the Notes app: the per-deployment settings
// (deploy.properties) and the manifest overlay with each app's https app-link filters.
plugins {
    `kotlin-dsl`
}

repositories {
    mavenCentral()
    gradlePluginPortal()
}
