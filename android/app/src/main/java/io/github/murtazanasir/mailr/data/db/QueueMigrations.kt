package io.github.murtazanasir.mailr.data.db

import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

/**
 * Version 6 → 7 (offline queue and local drafts, 0.5.3). The queue gains a state and a time for
 * the next try; the composer's local copies get their own table. Every row stays: the offline
 * queue, pending self-sends, reminders and the caches.
 */
val MIGRATION_6_7 = object : Migration(6, 7) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("ALTER TABLE `pending_ops` ADD COLUMN `state` TEXT NOT NULL DEFAULT 'QUEUED'")
        db.execSQL("ALTER TABLE `pending_ops` ADD COLUMN `nextAttemptAt` INTEGER NOT NULL DEFAULT 0")
        db.execSQL("CREATE TABLE IF NOT EXISTS `local_drafts` (`id` TEXT NOT NULL, `draftId` INTEGER, `payload` TEXT NOT NULL, " +
            "`subject` TEXT NOT NULL, `version` INTEGER NOT NULL, `syncedVersion` INTEGER NOT NULL, `closed` INTEGER NOT NULL, " +
            "`error` TEXT, `updatedAt` INTEGER NOT NULL, PRIMARY KEY(`id`))")
    }
}

/**
 * Version 7 → 8 (0.5.9): a "Send to self" share keeps the labels picked on its card, so a share
 * queued offline sends them when it goes. Every row stays.
 */
val MIGRATION_7_8 = object : Migration(7, 8) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("ALTER TABLE `self_sends` ADD COLUMN `labelIds` TEXT NOT NULL DEFAULT ''")
    }
}

/** 8 → 9: the Notes module's tables (notes, items, sections, labels, tables, rows, its replay queue and small values). */
val MIGRATION_8_9 = object : Migration(8, 9) {
    override fun migrate(db: SupportSQLiteDatabase) {
        NOTES_SCHEMA_9.forEach { db.execSQL(it) }
    }
}

/** The CREATE statements of the notes_* tables, as Room exports them (schemas/…/9.json). */
internal val NOTES_SCHEMA_9 = listOf(
    "CREATE TABLE IF NOT EXISTS `notes_notes` (`id` TEXT NOT NULL, `json` TEXT NOT NULL, `deleted` INTEGER NOT NULL, PRIMARY KEY(`id`))",
    "CREATE TABLE IF NOT EXISTS `notes_items` (`id` TEXT NOT NULL, `noteId` TEXT NOT NULL, `json` TEXT NOT NULL, `removed` INTEGER NOT NULL, `conflict` TEXT, PRIMARY KEY(`id`))",
    "CREATE INDEX IF NOT EXISTS `index_notes_items_noteId` ON `notes_items` (`noteId`)",
    "CREATE TABLE IF NOT EXISTS `notes_sections` (`id` TEXT NOT NULL, `noteId` TEXT NOT NULL, `json` TEXT NOT NULL, `removed` INTEGER NOT NULL, PRIMARY KEY(`id`))",
    "CREATE INDEX IF NOT EXISTS `index_notes_sections_noteId` ON `notes_sections` (`noteId`)",
    "CREATE TABLE IF NOT EXISTS `notes_labels` (`name` TEXT NOT NULL, `json` TEXT NOT NULL, PRIMARY KEY(`name`))",
    "CREATE TABLE IF NOT EXISTS `notes_tables` (`id` TEXT NOT NULL, `json` TEXT NOT NULL, `deleted` INTEGER NOT NULL, PRIMARY KEY(`id`))",
    "CREATE TABLE IF NOT EXISTS `notes_rows` (`id` TEXT NOT NULL, `tableId` TEXT NOT NULL, `json` TEXT NOT NULL, `removed` INTEGER NOT NULL, `conflict` TEXT, PRIMARY KEY(`id`))",
    "CREATE INDEX IF NOT EXISTS `index_notes_rows_tableId` ON `notes_rows` (`tableId`)",
    "CREATE TABLE IF NOT EXISTS `notes_ops` (`id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, `key` TEXT NOT NULL, `method` TEXT NOT NULL, `path` TEXT NOT NULL, `body` TEXT NOT NULL, `kind` TEXT NOT NULL, `target` TEXT NOT NULL, `parent` TEXT NOT NULL, `label` TEXT NOT NULL, `createdAt` INTEGER NOT NULL, `attempts` INTEGER NOT NULL, `state` TEXT NOT NULL DEFAULT 'QUEUED', `error` TEXT, `creates` TEXT)",
    "CREATE INDEX IF NOT EXISTS `index_notes_ops_parent` ON `notes_ops` (`parent`)",
    "CREATE TABLE IF NOT EXISTS `notes_kv` (`key` TEXT NOT NULL, `value` TEXT NOT NULL, PRIMARY KEY(`key`))",
)
