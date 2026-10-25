import org.gradle.api.GradleException
import org.gradle.api.Project

/**
 * Unit-test JVM settings shared by both apps (app/ and notes/ build.gradle.kts). Each value is a
 * Gradle property (-Pmailr.testForks=12) or an environment variable (MAILR_TEST_FORKS, which
 * build.sh passes into the container); the property wins.
 */
object MailrTests {
    private fun setting(project: Project, property: String, env: String): String? =
        (project.findProperty(property) as String?)?.takeIf { it.isNotBlank() }
            ?: System.getenv(env)?.takeIf { it.isNotBlank() }

    /**
     * Test JVMs at once in this module (`mailr.testForks.app`, `mailr.testForks`, MAILR_TEST_FORKS).
     * Both apps' test tasks run side by side and the Notes app's (112 tests) ends long before mailr's
     * (1,770), so mailr gets three eighths of the cores (at most 12) and Notes one eighth (at most 4):
     * 16 JVMs on the workstation's 16 physical cores. More JVMs made no run faster, and from 24 on the
     * wall-clock budgets of the tests that start first ran out.
     */
    fun forks(project: Project): Int = ((project.findProperty("mailr.testForks.${project.name}") as String?)
        ?: setting(project, "mailr.testForks", "MAILR_TEST_FORKS"))?.toInt()
        ?: Runtime.getRuntime().availableProcessors().let { cores ->
            if (project.name == "app") (cores * 3 / 8).coerceIn(1, 12) else (cores / 8).coerceIn(1, 4)
        }

    /**
     * Classes per test JVM before Gradle replaces it (`mailr.testForkEvery`, MAILR_TEST_FORK_EVERY).
     * 0, never: TestGraph and NotesTestGraph fail the test that leaks a coroutine or breaks Compose,
     * so a fresh JVM no longer has to protect the classes after it, and each restart cost 5 to 6 s.
     */
    fun forkEvery(project: Project): Long = setting(project, "mailr.testForkEvery", "MAILR_TEST_FORK_EVERY")?.toLong() ?: 0L

    /**
     * Whether DocsShotsTest runs (`-Pmailr.docsShots=true`). It records the
     * docs screenshots and compares nothing, so the normal runs and the release gate leave it out.
     */
    fun docsShots(project: Project): Boolean = project.findProperty("mailr.docsShots")?.toString() == "true"

    /**
     * The scale at which screenshot references are recorded and compared (`mailr.shotScale`, default
     * 0.5): Roborazzi's `roborazzi.record.resizeScale`, the default of every RoborazziOptions. Half size
     * keeps a one-word text change and a 1 dp shift visible to the comparison at a quarter of the
     * bytes. The docs shots (DOCS_SHOT) set 1.0 for
     * themselves: the docs site shows them.
     */
    fun shotScale(project: Project): String = project.findProperty("mailr.shotScale")?.toString()?.takeIf { it.isNotBlank() } ?: "0.5"

    /**
     * A screenshot comparison (`-Proborazzi.test.verify=true` or `-Proborazzi.test.compare=true`) needs
     * the reference images in this module's screenshots/ folder. The public repository leaves them out,
     * so there the build stops with a message instead of failing every screenshot test. Without either
     * property the tests still render every screen and compare nothing.
     */
    fun requireReferences(project: Project) {
        val compare = listOf("roborazzi.test.verify", "roborazzi.test.compare")
            .any { project.findProperty(it)?.toString() == "true" }
        val refs = project.file("screenshots")
        if (compare && !refs.isDirectory) throw GradleException(
            "${project.path}: a screenshot comparison needs the reference images in $refs, which this checkout " +
                "does not have. Run the unit tests without -Proborazzi.test.verify, or record references first " +
                "(recordRoborazziDebug) and compare later changes with them."
        )
    }
}
