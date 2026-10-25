package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.data.db.DESTRUCTIVE_FROM
import io.github.murtazanasir.mailr.data.db.MIGRATIONS
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The database no longer falls back to a destructive rebuild on every upgrade, which would
 * wipe pending_ops, self_sends and local_drafts (the user's unsent work). Every version from
 * the first migration up to the current schema (the newest exported schemas/…/N.json) needs a
 * migration; only versions older than that are rebuilt. A schema bump without a migration fails
 * here, not on a phone.
 */
class MigrationChainTest {
    private val current: Int = File("schemas/io.github.murtazanasir.mailr.data.db.MailrDb").listFiles()!!
        .mapNotNull { it.name.removeSuffix(".json").toIntOrNull() }.max()

    @Test
    fun everyVersionAfterTheRebuildRangeHasAMigrationToTheNext() {
        val steps = MIGRATIONS.map { it.startVersion to it.endVersion }
        val first = steps.minOf { it.first }
        for (v in first until current) {
            assertTrue("no migration from $v to ${v + 1}", (v to v + 1) in steps)
        }
        assertEquals("migrations end at the current schema", current, steps.maxOf { it.second })
    }

    @Test
    fun onlyVersionsBeforeTheFirstMigrationAreRebuilt() {
        val first = MIGRATIONS.minOf { it.startVersion }
        assertEquals((1 until first).toList(), DESTRUCTIVE_FROM.toList())
    }
}
