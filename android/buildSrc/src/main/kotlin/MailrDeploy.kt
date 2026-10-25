import org.gradle.api.DefaultTask
import org.gradle.api.Project
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.provider.ListProperty
import org.gradle.api.provider.Property
import org.gradle.api.tasks.Input
import org.gradle.api.tasks.OutputFile
import org.gradle.api.tasks.TaskAction
import java.util.Properties

/**
 * Per-deployment settings: android/deploy.properties (gitignored; see deploy.properties.example),
 * each key overridable by an environment variable. Unset, an app has no https app links (its custom
 * scheme still works) and the server field starts empty. Both apps read the same file.
 */
object MailrDeploy {
    private fun props(project: Project): Properties = Properties().apply {
        val f = project.rootProject.file("deploy.properties")
        if (f.exists()) f.inputStream().use { load(it) }
    }

    fun setting(project: Project, key: String, env: String): String =
        (System.getenv(env)?.takeIf { it.isNotBlank() } ?: props(project).getProperty(key)).orEmpty().trim()

    /** Hosts whose https links open in the apps (`appLinkHosts`, MAILR_APP_LINK_HOSTS). */
    fun appLinkHosts(project: Project): List<String> {
        val hosts = setting(project, "appLinkHosts", "MAILR_APP_LINK_HOSTS")
            .split(',', ' ').map { it.trim() }.filter { it.isNotEmpty() }.distinct()
        hosts.forEach { require(Regex("^[A-Za-z0-9.-]+$").matches(it)) { "appLinkHosts: not a host name: $it" } }
        return hosts
    }

    /** The server address the first-run screen starts with (`defaultServer`, MAILR_DEFAULT_SERVER). */
    fun defaultServer(project: Project): String {
        val s = setting(project, "defaultServer", "MAILR_DEFAULT_SERVER")
        require(s.none { it == '"' || it == '\\' }) { "defaultServer: quotes and backslashes are not allowed" }
        return s
    }
}

/**
 * Writes a manifest overlay with one activity's https app-link filters, one <data> per host and
 * path. [filters] holds "comment<TAB>path,path,…" per intent filter. The overlay merges into the
 * app's src/main/AndroidManifest.xml; with no hosts it adds nothing.
 */
abstract class AppLinksManifest : DefaultTask() {
    @get:Input abstract val hosts: ListProperty<String>
    @get:Input abstract val activity: Property<String>
    @get:Input abstract val filters: ListProperty<String>
    @get:OutputFile abstract val manifest: RegularFileProperty

    @TaskAction
    fun write() {
        fun filter(comment: String, paths: List<String>): String {
            val data = hosts.get().flatMap { h -> paths.map { p ->
                """                <data android:scheme="https" android:host="$h" android:pathPrefix="$p" />"""
            } }.joinToString("\n")
            return """
            |            <!-- $comment -->
            |            <intent-filter android:autoVerify="false">
            |                <action android:name="android.intent.action.VIEW" />
            |                <category android:name="android.intent.category.DEFAULT" />
            |                <category android:name="android.intent.category.BROWSABLE" />
            |$data
            |            </intent-filter>""".trimMargin()
        }
        val body = if (hosts.get().isEmpty()) "" else filters.get().joinToString("\n") { f ->
            val (comment, paths) = f.split('\t', limit = 2)
            filter(comment, paths.split(','))
        }
        val activityXml = if (body.isEmpty()) "" else """
            |        <activity android:name="${activity.get()}" android:exported="true">
            |$body
            |        </activity>""".trimMargin()
        manifest.get().asFile.writeText("""
            |<?xml version="1.0" encoding="utf-8"?>
            |<!-- Generated from deploy.properties (appLinkHosts) by buildSrc/MailrDeploy.kt. -->
            |<manifest xmlns:android="http://schemas.android.com/apk/res/android">
            |    <application>
            |$activityXml
            |    </application>
            |</manifest>
            |""".trimMargin())
    }
}
