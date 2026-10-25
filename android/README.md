# mailr for Android

This is the native Android client for mailr: Kotlin, Jetpack Compose and Material 3, with a minimum SDK of 29 (Android 10). It targets phones, the Pixel Fold (folded and unfolded) and tablets.

## Build

The host needs only Docker. Gradle, the SDK and the JDK run in `ghcr.io/cirruslabs/android-sdk:35`. Two named volumes speed up rebuilds:

- `mailr-gradle` holds the Gradle caches. Every checkout on the machine shares it, so builds take turns:
  `build.sh` holds `/tmp/mailr-android-build.lock` (flock) while it runs, and a second build waits.
- `mailr-android-home` holds the debug keystore, so debug builds install over each other.

```sh
./build.sh :app:assembleDebug          # app/build/outputs/apk/debug/app-debug.apk   (io.github.murtazanasir.mailr.debug)
./build.sh :app:assembleRelease        # app/build/outputs/apk/release/app-release.apk (io.github.murtazanasir.mailr, R8-minified, about 12.2 MB; the Notes app about 6.1 MB)
./build.sh :app:testDebugUnitTest      # unit + screenshot tests (screenshots taken, not compared)
./build.sh :app:recordRoborazziDebug   # re-record app/screenshots/*.png
./build.sh :app:verifyRoborazziDebug   # fail on visual changes
./build.sh assembleRelease             # both apps: app-release.apk and notes/build/outputs/apk/release/notes-release.apk
./build.sh :notes:assembleDebug        # the Notes app alone (io.github.murtazanasir.mailr.notes.debug)
./build.sh testDebugUnitTest           # the tests of both apps
```

Before a release, one command runs every unit test of both apps and compares every screenshot with
its reference. A changed or missing reference fails it:

```sh
./build.sh testDebugUnitTest -Proborazzi.test.verify=true --rerun
```

`-Proborazzi.test.verify=true` is the Roborazzi plugin's own switch: `verifyRoborazziDebug` is the
same test task with it set.

The public repository does not hold the reference images (`app/screenshots`, `app/screenshots-material`
and `notes/screenshots`), and there a comparison stops the build with a message
(`buildSrc/MailrTests.kt`). To check a change visually, record references before it
(`./build.sh :app:recordRoborazziDebug`) and compare after it. `testDebugUnitTest` alone renders
every screen and compares nothing, as CI does. `--rerun` forces the test tasks to run and leaves compilation to the
build cache; never use `--rerun-tasks`, which recompiles everything.

`DocsShotsTest` records the docs screenshots and compares nothing, so the runs above leave it out.
`-Pmailr.docsShots=true` brings it back.

The application ids are `io.github.murtazanasir.mailr` and `io.github.murtazanasir.mailr.notes`
(`buildSrc/MailrIds.kt`), and the Kotlin packages are `io.github.murtazanasir.mailr…`.

### The look

Both apps wear the web app's Classic look by default.
In mailr, Settings → Look → Style switches to Android's Material at once (`Prefs.look`); the Notes app
has the web look only. The look lives in `io.github.murtazanasir.mailr.look` (`:notes-core`): the tokens
`tools/android_tokens.py` generates from `frontend/src/styles` (`tools/generate`), the theme and the
components. Each component has the name and parameters of the Material 3 one it stands for, so a screen
switches by its import: import `io.github.murtazanasir.mailr.look.Button`, not `androidx.compose.material3.Button`.
Two have no Material twin: `SelectField` (a choice that opens a menu: the web's select, a list row in
Material) and `bannerColors` (a banner's tint: info, warning, danger or neutral in the web look, the
screen's own colours in Material). Pickers keep Material's `DatePickerState` and `TimePickerState`.

Every screenshot reference is in the web look; `MaterialLookShotsTest` keeps a few main screens in
Material (`app/screenshots-material`).

## Modules

- `:app`: the mailr app.
- `:notes`: the separate Notes app (application id `io.github.murtazanasir.mailr.notes`, its own version, 1.0.0 first).
  Its own Room database (`notes.db`), device session and OIDC
  redirect (`io.github.murtazanasir.mailr.notes:/oauth2redirect`), widgets (Glance), launcher shortcuts, a Quick Settings
  tile and a share target.
- `:notes-core`: the notes and tables data, sync, replay queue, item grammar and screens both apps run.
- `:session-core`: the API client, the device session, OIDC and password sign-in, the sign-in screen and the
  Notes module's transport, shared by both apps.
- `:notes-testing`: the fake notes server for both apps' unit tests.
- `buildSrc`: the per-deployment settings (`deploy.properties`) and the app-link manifest overlay, for both apps.

Release signing reads `keystore.properties`, which has `storeFile`, `storePassword`, `keyAlias` and `keyPassword`. Both that file and `keystore/` are gitignored; keep a backup of them, because updates must be signed with the same key. When the file is absent, the release APK is built unsigned.

Per-deployment settings live in `deploy.properties`, also gitignored. Copy `deploy.properties.example` and set:

- `appLinkHosts`: comma-separated hosts whose https links open in the app (`/c/`, `/mail/`, `/calendar`, `/tasks`; in the Notes app `/notes/n/` and `/tables/t/`). The build generates the intent filters into a manifest overlay. Without hosts there are no https app links; the `mailr://` links still work.
- `defaultServer`: the address the first-run screen starts with. Without it the field starts empty.

The environment variables `MAILR_APP_LINK_HOSTS` and `MAILR_DEFAULT_SERVER` override the file; `build.sh` passes them into the container.

```sh
MAILR_APP_LINK_HOSTS=mail.example.com ./build.sh :app:assembleRelease
```

## Install

```sh
adb install -r app/build/outputs/apk/release/app-release.apk
```

The debug and release builds install side by side. Both register the same OAuth redirect scheme, so with both installed, Android asks which app should finish a Pocket ID sign-in. Keep one of them installed on a daily phone.

## First run

1. Enter the server address. The field starts with `defaultServer` from `deploy.properties` (empty without it) and stays editable.
2. The app reads `/api/v1/auth/native-config`:
   - When the server has a native client configured, the app shows **Sign in with Pocket ID**. That opens a Custom Tab (AppAuth, PKCE), and the ID token is exchanged for a device session.
   - Otherwise it shows email and password.
3. The session token is stored in EncryptedSharedPreferences. It shows up in the web UI under Settings → Sessions as "android".

For the server side (the OIDC client and its variables), see the admin guide: site/docs/admin/phone-apps.md and site/docs/admin/sign-in.md.

## Layout

```
data/api       OkHttp + kotlinx.serialization client (cookie + CSRF, Idempotency-Key), DTOs
data/auth      SessionStore (encrypted), AuthManager (AppAuth → /auth/oidc/native)
data/db        Room: list pages per view, conversations, bodies, pending ops (outbox)
data/repo      MailRepository (Paging 3 RemoteMediator, optimistic actions, outbox drain), ComposeRepository
data/sync      LiveEvents (SSE /events), OutboxWorker + SyncWorker (WorkManager), notifications
data/agent     Assistant SSE client
data/share     Share targets: ShareParser, "Send to self" (SelfSendRepository + SelfSendWorker, sharing shortcut)
data/cal       CalendarRepository (Room cache, optimistic writes on the shared offline queue), sync workers
data/reminders Reminder planning, AlarmManager, notifications
ui/calendar    Calendar (schedule/day/week/month), Tasks, event and task editors, snooze sheet
ui/            Compose screens; App.kt holds the root and the home screen, MainShell.kt the tabs,
               MailRoutes.kt the Mail tab, HomeRoutes.kt and AppRoutes.kt the other screens, DeepLinks.kt the links
```

### Adaptive behaviour

- `NavigationSuiteScaffold` shows a bottom bar on compact widths (phone, folded Fold) and a rail on medium and expanded widths. The bottom bar hides while the keyboard is open.
- Mail uses `ListDetailPaneScaffold`: one pane on phones, list and conversation side by side on the unfolded Fold and tablets. The split follows the hinge in book posture.
- The activity is edge-to-edge with `adjustResize`. Screens pad for the IME, so the focused field and the send action stay visible.

### Offline

- The list and bodies are served from Room first.
- Actions (archive, star, label, snooze…) apply optimistically.
  - When the network fails, they queue in `pending_ops` with their Idempotency-Key.
  - `OutboxWorker` replays them with the same key once the device is online. A replay therefore cannot apply twice.
  - Operations the server rejects permanently (4xx) are dropped, with a message.

### Sharing

- **Send to self**: a share target and a Direct Share shortcut.
  - It sends straight to the default identity; no composer opens.
  - A small card confirms it and offers Undo (the server's undo-send hold) and *Add a note*.
  - Offline, it queues in `self_sends` and WorkManager sends it later.
  - The subject comes from EXTRA_SUBJECT, then EXTRA_TITLE, then the page title (fetched for bare links), then the first line, then the URL.
  - From the launcher shortcut, it opens a quick note to self.
- **New email**: the composer, prefilled with what was shared. It runs in the sharing app's task and returns there after Send.

### Calendar and tasks

- **Calendar** has schedule, day, week and month views. Swipe to change the period and tap an empty slot to create an event. When unfolded, event details and the editor open in a side pane.
- **Tasks** are grouped by due date (Overdue … Completed), with subtasks under their parent.
  - Check off with the animated circle, or swipe right to complete and left to delete. Both offer Undo.
  - Quick add reads "call bank tomorrow 3pm !!".
- **Offline**: create and edit work offline, as with mail. Items get client-chosen UIDs, so something created offline can be edited before it syncs.
- **Reminders**: exact local alarms, grouped notifications, and snooze/done from the lock screen.

## Tests

- `RepositoryTest` (MockWebServer):
  - paging through the cache;
  - optimistic actions carrying CSRF and Idempotency-Key;
  - offline queueing and replay with the same key;
  - dropping rejected operations.
- `UtilTest`: date formats, participants, HTML round trip, deep links, snippet cleanup.
- `SelfSendTest`: share parsing, subject rules, and the send/undo/note/offline pipeline.
- `ReminderPlanTest`, `ReminderEngineTest`: reminder planning and diffing, grouping, snooze times, boot/missed.
- `CalendarLogicTest`: RRULE round trips, quick add, time-grid layout, task sections, request bodies.
- `DeviceShots` and its classes `Device{MailList,MailRead,Assistant,Calendar,Settings}ShotsTest` (Roborazzi + Robolectric):
  - 23 screens × {phone 411×891, Fold unfolded 841×701, tablet 1280×800} × {light, dark}; the folded Fold
    (411×797) is as wide as the phone and has its layout, so it has no shots of its own;
  - images in `app/screenshots/`, named `<device>-<theme>-<screen>.png` (the citation banner on the phone only;
    list and detail on the Fold and the tablet only, since on a phone it is the conversation);
  - references are recorded and compared at half size (`roborazzi.record.resizeScale`, `buildSrc/MailrTests.kt`);
  - one class per area, so the test JVMs share the shots out.
- `AgendaShots` and `Agenda{Styles,Layout,Days}ShotsTest`: the Agenda widget, split the same way.
  - Robolectric does not render WebView content. Check message bodies on a device or emulator.
