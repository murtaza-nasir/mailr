package io.github.murtazanasir.mailr.data.db

import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

/**
 * Version 5 → 6 (calendar build). Adds columns to `cal_events` and `cal_tasks` and four new
 * tables. Existing rows stay: the offline queue, pending self-sends, reminder states and the
 * calendar cache all survive. Event rows keep their old keys until the next sync replaces them;
 * reminder keys in the old forms are normalised by the reminder engine on its next pass
 * ([io.github.murtazanasir.mailr.data.reminders.ReminderPlan.normalize]), where a date parser is at hand.
 */
val MIGRATION_5_6 = object : Migration(5, 6) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("ALTER TABLE `cal_events` ADD COLUMN `occurrence` TEXT")
        db.execSQL("ALTER TABLE `cal_events` ADD COLUMN `objectId` INTEGER")
        db.execSQL("ALTER TABLE `cal_events` ADD COLUMN `calendarId` INTEGER")
        db.execSQL("ALTER TABLE `cal_tasks` ADD COLUMN `objectId` INTEGER")
        db.execSQL("ALTER TABLE `cal_tasks` ADD COLUMN `listId` INTEGER")
        db.execSQL("ALTER TABLE `cal_tasks` ADD COLUMN `startMs` INTEGER")
        db.execSQL("ALTER TABLE `cal_tasks` ADD COLUMN `sortOrder` INTEGER")
        db.execSQL("ALTER TABLE `cal_tasks` ADD COLUMN `etag` TEXT")
        db.execSQL("CREATE TABLE IF NOT EXISTS `cal_collections` (`url` TEXT NOT NULL, `calendarId` INTEGER, " +
            "`name` TEXT NOT NULL, `color` TEXT, `events` INTEGER NOT NULL, `tasks` INTEGER NOT NULL, `hidden` INTEGER NOT NULL, " +
            "`sortOrder` INTEGER NOT NULL, `deviceHidden` INTEGER, `json` TEXT NOT NULL, PRIMARY KEY(`url`))")
        db.execSQL("CREATE TABLE IF NOT EXISTS `cal_event_masters` (`uid` TEXT NOT NULL, `objectId` INTEGER, " +
            "`calendar` TEXT, `etag` TEXT, `json` TEXT NOT NULL, PRIMARY KEY(`uid`))")
        db.execSQL("CREATE TABLE IF NOT EXISTS `cal_tags` (`name` TEXT NOT NULL, `color` TEXT, `ord` INTEGER NOT NULL, " +
            "PRIMARY KEY(`name`))")
        db.execSQL("CREATE TABLE IF NOT EXISTS `cal_conflicts` (`key` TEXT NOT NULL, `kind` TEXT NOT NULL, `uid` TEXT NOT NULL, " +
            "`method` TEXT NOT NULL, `path` TEXT NOT NULL, `body` TEXT NOT NULL, `serverJson` TEXT, `detail` TEXT NOT NULL, " +
            "`createdAt` INTEGER NOT NULL, PRIMARY KEY(`key`))")
    }
}
