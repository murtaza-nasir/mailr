package io.github.murtazanasir.mailr

import com.github.takahirom.roborazzi.ExperimentalRoborazziApi
import com.github.takahirom.roborazzi.RoborazziOptions
import com.github.takahirom.roborazzi.RoborazziOptions.RecordOptions
import com.github.takahirom.roborazzi.RoborazziTaskType

/**
 * Docs screenshots (site/shots.yaml) are always recorded, never compared.
 * Their files under screenshots/docs/ are local output that git ignores; the copies that count are
 * the committed ones in site/docs/assets/shots. Comparing against a stale local copy failed
 * verifyRoborazzi whenever the copy on that machine was older than the code. They are recorded at
 * full size; the references are half size (`roborazzi.record.resizeScale`, buildSrc/MailrTests.kt).
 */
@OptIn(ExperimentalRoborazziApi::class)
internal val DOCS_SHOT = RoborazziOptions(taskType = RoborazziTaskType.Record, recordOptions = RecordOptions(resizeScale = 1.0))
