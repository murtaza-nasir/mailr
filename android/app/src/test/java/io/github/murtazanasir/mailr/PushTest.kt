package io.github.murtazanasir.mailr

import android.Manifest
import android.app.Activity
import android.app.Application
import android.app.Notification
import android.app.NotificationManager
import android.content.ComponentName
import android.content.IntentFilter
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.ApprovalPreview
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.Outbox
import io.github.murtazanasir.mailr.data.api.Participant
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.push.PUSH_EVENTS
import io.github.murtazanasir.mailr.data.push.PushDistributors
import io.github.murtazanasir.mailr.data.push.PushEvent
import io.github.murtazanasir.mailr.data.push.PushHandler
import io.github.murtazanasir.mailr.data.push.PushPayload
import io.github.murtazanasir.mailr.data.push.PushRegistrar
import io.github.murtazanasir.mailr.data.push.PushStatus
import io.github.murtazanasir.mailr.data.push.PushStore
import io.github.murtazanasir.mailr.data.push.UnifiedPushDistributors
import io.github.murtazanasir.mailr.data.settings.NotifyMode
import io.github.murtazanasir.mailr.data.sync.Channels
import io.github.murtazanasir.mailr.data.sync.Notifier
import io.github.murtazanasir.mailr.data.sync.pollNewMail
import io.github.murtazanasir.mailr.ui.Destination
import io.github.murtazanasir.mailr.ui.MainActivity
import io.github.murtazanasir.mailr.ui.parseDeepLink
import com.google.crypto.tink.apps.fixed_webpush.WebPushHybridDecrypt
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.unifiedpush.android.connector.FailedReason
import org.unifiedpush.android.connector.data.PublicKeySet
import org.unifiedpush.android.connector.data.ResolvedDistributor
import org.unifiedpush.android.connector.keys.KeyManager
import java.util.Base64

/**
 * Push through UnifiedPush (3 Oct 2026): registration with the distributor, this phone's push
 * target on mailr (POST, replace, DELETE), the poll interval, each event's JSON from
 * backend/mailr/core/push.py turned into the app's own notification, and no duplicates
 * between a push message and the periodic poll.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class PushTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    private val app: Application = ApplicationProvider.getApplicationContext()
    private lateinit var fake: FakePushServer
    private lateinit var g: AppGraph
    private val dist = FakeDistributors()
    private val uploads = mutableListOf<Unit>()
    private val polls = mutableListOf<Int>()
    private lateinit var push: PushRegistrar
    private var clock = 1_000_000_000_000L

    /** A distributor list the test controls (the real one asks the package manager). */
    class FakeDistributors : PushDistributors {
        var installed = listOf<String>()
        var default: String? = null
        var acked: String? = null
        var saved: String? = null
        var registered = 0
        var unregistered = 0
        /** The VAPID key each registration carried (null: unsigned). */
        val vapids = mutableListOf<String?>()
        override fun current() = acked?.takeIf { it in installed }
        override fun resolveDefault(): ResolvedDistributor = when {
            installed.isEmpty() -> ResolvedDistributor.NoneAvailable
            installed.size == 1 -> ResolvedDistributor.Found(installed[0])
            default != null -> ResolvedDistributor.Found(default!!)
            else -> ResolvedDistributor.ToSelect
        }
        override fun save(pkg: String) { saved = pkg }
        override fun register(vapid: String?) { registered++; vapids += vapid }
        override fun unregister() { unregistered++ }
        override fun name(pkg: String) = if (pkg == NTFY) "ntfy" else pkg
        override fun chooseDefault(activity: Activity, done: (Boolean) -> Unit) { default = installed.firstOrNull(); done(default != null) }
    }

    @Before fun setUp() {
        fake = FakePushServer()
        SessionStore(app).apply { server = fake.url(); save("tok", "csrf", "me@example.com", "Me") }
        shadowOf(app).grantPermissions(Manifest.permission.POST_NOTIFICATIONS)
        Channels.create(app)
        g = graphs.graph(app)
        push = PushRegistrar(g.api, PushStore(app), dist, signedIn = { g.sessions.session.value != null },
            enqueueUpload = { uploads += Unit }, pollEvery = { polls += it }, device = "Pixel 8", now = { clock })
        g.push = push
    }

    @After fun tearDown() {
        graphs.close()
        fake.shutdown()
    }

    private val nm get() = app.getSystemService(NotificationManager::class.java)
    private fun shown(tag: String, id: Int): Notification? = shadowOf(nm).getNotification(tag, id)
    private fun text(n: Notification, key: String) = n.extras.getCharSequence(key)?.toString()

    // ---------------- registration ----------------

    @Test fun noPushAppMeansPollingEvery15Minutes() {
        push.start()
        assertEquals(PushStatus.NO_DISTRIBUTOR, push.info.value.status)
        assertEquals(0, dist.registered)
        assertEquals(15, push.pollMinutes())
        assertTrue(fake.calls.isEmpty())
    }

    @Test fun severalPushAppsAndNoDefaultAskOnce() {
        dist.installed = listOf(NTFY, "org.example.other")
        push.start()
        assertEquals(PushStatus.CHOOSE, push.info.value.status)
        assertEquals(0, dist.registered)
        // Settings → Push → Choose: the system chooser picks one, then mailr registers with it.
        push.choose(org.robolectric.Robolectric.buildActivity(Activity::class.java).get())
        assertEquals(NTFY, dist.saved)
        assertEquals(1, dist.registered)
        assertEquals(PushStatus.REGISTERING, push.info.value.status)
    }

    @Test fun theDefaultPushAppRegistersAndTheEndpointIsPosted() = runBlocking {
        dist.installed = listOf(NTFY)
        push.start()
        assertEquals(NTFY, dist.saved)
        assertEquals(1, dist.registered)
        assertEquals("ntfy", push.info.value.distributorName)
        assertEquals(PushStatus.REGISTERING, push.info.value.status)

        // The distributor answers with an endpoint and the connector's Web Push keys.
        push.onNewEndpoint(EP1, PUB, AUTH)
        assertEquals(1, uploads.size)
        assertTrue(push.upload())
        val post = fake.posts("/api/v1/push/targets").single().json
        assertEquals(EP1, post["endpoint"]!!.jsonPrimitive.content)
        assertEquals("unifiedpush", post["kind"]!!.jsonPrimitive.content)
        assertEquals("Pixel 8", post["device"]!!.jsonPrimitive.content)
        assertEquals(JsonArray(PUSH_EVENTS.map { JsonPrimitive(it) }), post["events"])
        assertEquals(PUB, post["keys"]!!.jsonObject["p256dh"]!!.jsonPrimitive.content)
        assertEquals(AUTH, post["keys"]!!.jsonObject["auth"]!!.jsonPrimitive.content)
        assertFalse("events never include reminders: the phone fires its own", "reminder" in PUSH_EVENTS)
        val info = push.info.value
        assertEquals(PushStatus.CONNECTED, info.status)
        assertEquals(fake.targets[EP1], info.targetId)
        // Push works: the poll slows to once an hour.
        assertEquals(listOf(60), polls)
        assertEquals(60, push.pollMinutes())

        // App start again: the same endpoint comes back, nothing is posted, still connected.
        dist.acked = NTFY
        push.start()
        push.onNewEndpoint(EP1, PUB, AUTH)
        assertEquals(1, uploads.size)
        assertEquals(PushStatus.CONNECTED, push.info.value.status)
        assertEquals(1, fake.posts("/api/v1/push/targets").size)
    }

    @Test fun aNewEndpointReplacesThisPhonesTarget() = runBlocking {
        dist.installed = listOf(NTFY)
        push.start()
        push.onNewEndpoint(EP1, PUB, AUTH)
        push.upload()
        val first = push.info.value.targetId!!
        push.onNewEndpoint(EP2, PUB, AUTH)
        assertEquals(PushStatus.REGISTERING, push.info.value.status)
        assertTrue(push.upload())
        val second = push.info.value.targetId!!
        assertTrue(first != second)
        assertEquals(listOf(first), fake.deletes())
        assertEquals(mapOf(EP2 to second), fake.targets.toMap())
        assertEquals(PushStatus.CONNECTED, push.info.value.status)
    }

    @Test fun anUploadWithoutNetworkIsRetried() = runBlocking {
        dist.installed = listOf(NTFY)
        push.start()
        push.onNewEndpoint(EP1, PUB, AUTH)
        fake.shutdown()
        assertFalse(push.upload())  // the worker retries
        assertEquals(PushStatus.REGISTERING, push.info.value.status)
        assertEquals(15, push.pollMinutes())
    }

    @Test fun signOutDeletesTheTargetAndLeavesThePushApp() = runBlocking {
        dist.installed = listOf(NTFY)
        push.start(); push.onNewEndpoint(EP1, PUB, AUTH); push.upload()
        val id = push.info.value.targetId!!
        push.signOut()
        assertEquals(listOf(id), fake.deletes())
        assertEquals(1, dist.unregistered)
        assertEquals(PushStatus.IDLE, push.info.value.status)
        assertNull(push.info.value.targetId)
        assertEquals(15, polls.last())
    }

    @Test fun unregisteredByThePushAppDeletesTheTarget() = runBlocking {
        dist.installed = listOf(NTFY)
        push.start(); push.onNewEndpoint(EP1, PUB, AUTH); push.upload()
        val id = push.info.value.targetId!!
        dist.installed = emptyList()  // ntfy uninstalled
        push.onUnregistered()
        assertEquals(listOf(id), fake.deletes())
        assertEquals(PushStatus.NO_DISTRIBUTOR, push.info.value.status)
        assertEquals(listOf(60, 15), polls)
    }

    @Test fun registrationFailureFallsBackToPollingAndRetryRegisters() {
        dist.installed = listOf(NTFY)
        push.start()
        push.onRegistrationFailed(FailedReason.NETWORK)
        assertEquals(PushStatus.FAILED, push.info.value.status)
        assertEquals("No connection to the push server", push.info.value.error)
        assertEquals(15, push.pollMinutes())
        push.retry()
        assertEquals(2, dist.registered)
        assertEquals(PushStatus.REGISTERING, push.info.value.status)
    }

    @Test fun aMessageAfterAFailureMeansPushWorksAgain() = runBlocking {
        dist.installed = listOf(NTFY)
        push.start(); push.onNewEndpoint(EP1, PUB, AUTH); push.upload()
        push.onTempUnavailable()
        assertEquals(PushStatus.FAILED, push.info.value.status)
        push.onMessage()
        assertEquals(PushStatus.CONNECTED, push.info.value.status)
        assertNotNull(push.info.value.lastMessageAt)
        assertEquals(listOf(60, 15, 60), polls)
    }

    @Test fun sendTestPostsToTheTarget() = runBlocking {
        dist.installed = listOf(NTFY)
        assertFalse(push.test().ok)  // not registered yet: nothing to test
        push.start(); push.onNewEndpoint(EP1, PUB, AUTH); push.upload()
        val id = push.info.value.targetId!!
        assertTrue(push.test().ok)
        assertEquals(1, fake.posts("/api/v1/push/targets/$id/test").size)
        fake.testAnswer = """{"ok":false,"error":"the push server answered 500","removed":false}"""
        val r = push.test()
        assertFalse(r.ok)
        assertEquals("the push server answered 500", r.error)
    }

    /** The real connector finds an installed distributor and sends it REGISTER with mailr's token. */
    @Test fun theConnectorFindsAnInstalledDistributorAndRegisters() {
        val pm = shadowOf(app.packageManager)
        val receiver = ComponentName(NTFY, "$NTFY.up.BroadcastReceiver")
        // An exported receiver for the distributor's REGISTER action, as ntfy declares.
        pm.installPackage(android.content.pm.PackageInfo().apply {
            packageName = NTFY
            applicationInfo = android.content.pm.ApplicationInfo().apply { packageName = NTFY; name = "ntfy" }
        })
        pm.addOrUpdateReceiver(android.content.pm.ActivityInfo().apply {
            packageName = NTFY; name = receiver.className; exported = true; enabled = true
            applicationInfo = android.content.pm.ApplicationInfo().apply { packageName = NTFY }
        })
        pm.addIntentFilterForReceiver(receiver, IntentFilter("org.unifiedpush.android.distributor.REGISTER"))
        val real = UnifiedPushDistributors(app) { MemoryKeys() }
        val found = real.resolveDefault()
        assertEquals(ResolvedDistributor.Found(NTFY), found)
        real.save(NTFY)
        real.register(null)
        val reg = shadowOf(app).broadcastIntents.last { it.action == "org.unifiedpush.android.distributor.REGISTER" }
        assertEquals(NTFY, reg.`package`)
        assertTrue(reg.getStringExtra("token")!!.isNotBlank())
        assertEquals("Mailr: new mail and approvals", reg.getStringExtra("message"))
    }

    // ---------------- VAPID (signed UnifiedPush, server commit 2ae4e2c) ----------------

    @Test fun aServerWithAKeyGetsASignedRegistration() = runBlocking {
        fake.vapidKey = VAPID
        dist.installed = listOf(NTFY)
        push.start()
        assertEquals(listOf<String?>(null), dist.vapids)  // the key is not known before the first upload
        push.onNewEndpoint(EP1, PUB, AUTH)
        // The upload reads the key, and the unsigned endpoint is never posted: the phone registers again with it.
        assertTrue(push.upload())
        assertEquals(1, fake.gets("/api/v1/push/targets").size)
        assertEquals(listOf(null, VAPID), dist.vapids)
        assertTrue(fake.posts("/api/v1/push/targets").isEmpty())
        assertEquals(PushStatus.REGISTERING, push.info.value.status)

        // The distributor answers with the signed endpoint; this one is posted with vapid: true.
        push.onNewEndpoint(EP2, PUB, AUTH)
        assertTrue(push.upload())
        val post = fake.posts("/api/v1/push/targets").single().json
        assertEquals(EP2, post["endpoint"]!!.jsonPrimitive.content)
        assertEquals("true", post["vapid"]!!.jsonPrimitive.content)
        assertEquals(PUB, post["keys"]!!.jsonObject["p256dh"]!!.jsonPrimitive.content)
        assertEquals(PushStatus.CONNECTED, push.info.value.status)
        assertTrue(push.info.value.uploadedVapid)
        assertEquals(mapOf(EP2 to push.info.value.targetId), fake.targets.toMap())

        // App start: the saved key goes with the registration, and nothing more is read or posted.
        dist.acked = NTFY
        push.start()
        assertEquals(VAPID, dist.vapids.last())
        push.onNewEndpoint(EP2, PUB, AUTH)
        assertEquals(2, uploads.size)
        assertEquals(PushStatus.CONNECTED, push.info.value.status)
        assertEquals(1, fake.posts("/api/v1/push/targets").size)
    }

    @Test fun aServerWithoutAKeyKeepsTheUnsignedRegistration() = runBlocking {
        for (route in listOf(true, false)) {  // no vapid_public_key in the answer; no GET route at all
            fake.listRoute = route
            fake.targets.clear()
            push.store.clear()
            dist.vapids.clear()
            dist.installed = listOf(NTFY)
            push.start()
            push.onNewEndpoint(EP1, PUB, AUTH)
            assertTrue(push.upload())
            val post = fake.posts("/api/v1/push/targets").last().json
            assertFalse("an older server never sees vapid", post.containsKey("vapid"))
            assertEquals(PUB, post["keys"]!!.jsonObject["p256dh"]!!.jsonPrimitive.content)
            assertEquals(listOf<String?>(null), dist.vapids)
            assertEquals(PushStatus.CONNECTED, push.info.value.status)
            assertFalse(push.info.value.uploadedVapid)
        }
    }

    @Test fun aKeyIsNeverPostedWithoutTheConnectorsKeys() = runBlocking {
        fake.vapidKey = VAPID
        dist.installed = listOf(NTFY)
        push.start()
        push.onNewEndpoint(EP1, null, null)
        push.upload()  // registers again with the key
        push.onNewEndpoint(EP1, null, null)
        assertTrue(push.upload())
        val post = fake.posts("/api/v1/push/targets").single().json
        assertFalse(post.containsKey("vapid"))  // mailr refuses vapid without keys
        assertFalse(post.containsKey("keys"))
        assertEquals(PushStatus.CONNECTED, push.info.value.status)
    }

    @Test fun anUnsignedPhoneMovesToTheKeyOnceTheServerHasOne() = runBlocking {
        dist.installed = listOf(NTFY)
        push.start(); push.onNewEndpoint(EP1, PUB, AUTH); push.upload()
        val unsigned = push.info.value.targetId!!
        assertFalse(push.info.value.uploadedVapid)

        // The server is upgraded; the next app start within a day reads nothing.
        fake.vapidKey = VAPID
        dist.acked = NTFY
        push.start(); push.onNewEndpoint(EP1, PUB, AUTH)
        assertEquals(1, uploads.size)

        // A day later the app start reads the key again, registers with it and posts the signed endpoint.
        clock += PushRegistrar.KEY_RECHECK_MS
        push.start(); push.onNewEndpoint(EP1, PUB, AUTH)
        assertEquals(2, uploads.size)
        assertEquals(PushStatus.CONNECTED, push.info.value.status)  // still connected while it checks
        assertTrue(push.upload())
        assertEquals(VAPID, dist.vapids.last())
        push.onNewEndpoint(EP2, PUB, AUTH)
        assertTrue(push.upload())
        val post = fake.posts("/api/v1/push/targets").last().json
        assertEquals(EP2, post["endpoint"]!!.jsonPrimitive.content)
        assertEquals("true", post["vapid"]!!.jsonPrimitive.content)
        assertEquals(listOf(unsigned), fake.deletes())
        assertTrue(push.info.value.uploadedVapid)
        assertEquals(PushStatus.CONNECTED, push.info.value.status)
    }

    @Test fun aKeyReadThatFailsIsRetried() = runBlocking {
        dist.installed = listOf(NTFY)
        push.start()
        push.onNewEndpoint(EP1, PUB, AUTH)
        fake.vapidKey = VAPID
        fake.listStatus = 503
        assertFalse(push.upload())  // the worker retries; no unsigned post meanwhile
        assertTrue(fake.posts("/api/v1/push/targets").isEmpty())
        assertEquals(listOf<String?>(null), dist.vapids)
        // Back: the key is read and the phone registers with it.
        fake.listStatus = null
        assertTrue(push.upload())
        assertEquals(listOf(null, VAPID), dist.vapids)
    }

    /** The real connector puts mailr's key in the REGISTER broadcast as the distributor reads it. */
    @Test fun theConnectorSendsTheKeyToTheDistributor() {
        val pm = shadowOf(app.packageManager)
        val receiver = ComponentName(NTFY, "$NTFY.up.BroadcastReceiver")
        pm.installPackage(android.content.pm.PackageInfo().apply {
            packageName = NTFY
            applicationInfo = android.content.pm.ApplicationInfo().apply { packageName = NTFY; name = "ntfy" }
        })
        pm.addOrUpdateReceiver(android.content.pm.ActivityInfo().apply {
            packageName = NTFY; name = receiver.className; exported = true; enabled = true
            applicationInfo = android.content.pm.ApplicationInfo().apply { packageName = NTFY }
        })
        pm.addIntentFilterForReceiver(receiver, IntentFilter("org.unifiedpush.android.distributor.REGISTER"))
        val real = UnifiedPushDistributors(app) { MemoryKeys() }
        real.save(NTFY)
        real.register(VAPID)
        val reg = shadowOf(app).broadcastIntents.last { it.action == "org.unifiedpush.android.distributor.REGISTER" }
        assertEquals(VAPID, reg.getStringExtra("vapid"))
    }

    // ---------------- messages ----------------

    @Test fun eachEventsJsonDecodes() {
        // Exactly what backend/mailr/core/push.py sends (tests/test_push_unifiedpush.py).
        assertEquals(PushEvent.NewMail(4503599627370497, 4503599627370498, false, "Carol Diaz", "New message"),
            decode("""{"title": "Carol Diaz", "body": "New message", "conversation_id": 4503599627370497, "message_id": 4503599627370498, "click": "https://mail.example.com/mail/inbox/c1", "type": "new_mail"}"""))
        assertEquals(PushEvent.NewMail(7, 9, true, "mailr", "New message"),
            decode("""{"title": "mailr", "body": "New message", "conversation_id": "7", "message_id": 9, "type": "important_mail"}"""))
        val a = decode("""{"title": "Approval request from Claude Code", "body": "Send email · To dana@example.org", "pending_id": 41, "action": "mail.send", "click": "https://m/x", "type": "approval"}""") as PushEvent.Approval
        assertEquals("41", a.key)
        assertEquals("mail.send", a.action)
        // Without an `actor` field, the name is read from the title (current and earlier wording).
        assertEquals("Claude Code", PushHandler.actorFromTitle("Approval request from Claude Code"))
        assertEquals("Claude Code", PushHandler.actorFromTitle("Claude Code needs your approval"))
        val legacy = decode("""{"title": "Approve an agent's email", "body": "To dana@example.org.", "outbox_id": 12, "type": "approval"}""") as PushEvent.Approval
        assertEquals("o12", legacy.key)
        assertEquals(PushEvent.SendFailed(12, "Message not sent", "To dana@example.org: 550"),
            decode("""{"title": "Message not sent", "body": "To dana@example.org: 550", "outbox_id": 12, "click": "https://m/mail/scheduled", "type": "send_failed"}"""))
        assertEquals(PushEvent.Assistant(5, 3066, "Ambrose · Invoices", "2 new invoices"),
            decode("""{"title": "Ambrose · Invoices", "body": "2 new invoices", "run_id": 5, "order_id": 2, "conversation_id": 3066, "type": "assistant"}"""))
        assertEquals(PushEvent.Test("mailr", "Test notification"), decode("""{"title": "mailr", "body": "Test notification", "type": "test"}"""))
        assertEquals("reminder", (decode("""{"title": "x", "body": "y", "type": "reminder"}""") as PushEvent.Other).type)
        assertNull(PushPayload.decode("not json".toByteArray()))
        assertNull(PushPayload.decode("""{"title": "no type"}""".toByteArray()))
    }

    /** pywebpush's aes128gcm (core/webpush.encrypt) decrypts with the connector's own Tink decryptor. */
    @Test fun theServersEncryptionOpensWithTheConnectorsKeys() {
        val d = Base64.getUrlDecoder()
        val plain = WebPushHybridDecrypt.Builder()
            .withAuthSecret(d.decode(V_AUTH)).withRecipientPublicKey(d.decode(V_PUB)).withRecipientPrivateKey(d.decode(V_PRIV))
            .build().decrypt(d.decode(V_CT), null)
        assertEquals(PushEvent.NewMail(4503599627370497, 4503599627370498, false, "Carol Diaz", "New message"), PushPayload.decode(plain))
    }

    private fun decode(json: String) = PushPayload.decode(json.toByteArray())

    private val mail = ConversationSummary(3075, "Budget for Q3", "Can you send the numbers?", unreadCount = 1, important = true,
        participants = listOf(Participant("dana@example.org", "Dana Lee")), labelIds = listOf(1))

    @Test fun newMailIsReadFromTheServerAndNotifiedOnce() = runBlocking {
        fake.conversations = listOf(mail)
        val e = PushEvent.NewMail(3075, 9, true, "Dana Lee", "New message")
        PushHandler.handle(g, e, trusted = true)
        val n = shown("c3075", 1)!!
        assertEquals("Dana Lee", text(n, Notification.EXTRA_TITLE))
        assertEquals("Budget for Q3", text(n, Notification.EXTRA_TEXT))
        assertEquals(Channels.MAIL, n.channelId)
        assertEquals("mailr://conversation/3075", shadowOf(n.contentIntent).savedIntent.dataString)
        assertEquals(MainActivity::class.java.name, shadowOf(n.contentIntent).savedIntent.component!!.className)
        // Settings → New mail is "Important only" by default: the poll asked for that section.
        assertTrue(fake.calls.any { it.path.startsWith("/api/v1/conversations") && "section=important" in it.path })

        // The same message again, then the periodic poll: no second notification.
        shadowOf(nm).getAllNotifications().size.let { before ->
            PushHandler.handle(g, e, trusted = true)
            assertTrue(pollNewMail(g, NotifyMode.IMPORTANT))
            assertEquals(before, shadowOf(nm).getAllNotifications().size)
        }
        assertEquals(1, shadowOf(nm).getAllNotifications().count { it.extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() == "Dana Lee" })
    }

    @Test fun thePollFirstThenThePushDoesNotNotifyAgain() = runBlocking {
        fake.conversations = listOf(mail)
        assertTrue(pollNewMail(g, NotifyMode.IMPORTANT))
        nm.cancelAll()
        PushHandler.handle(g, PushEvent.NewMail(3075, 9, true, "Dana Lee", "New message"), trusted = true)
        assertNull(shown("c3075", 1))
    }

    @Test fun whenTheServerCannotBeReadTheSenderStillShows() = runBlocking {
        fake.down = true
        PushHandler.handle(g, PushEvent.NewMail(3075, 9, true, "Dana Lee", "New message"), trusted = true)
        val n = shown("c3075", 1)!!
        assertEquals("Dana Lee", text(n, Notification.EXTRA_TITLE))
        assertEquals("New message", text(n, Notification.EXTRA_TEXT))
        // Later the poll can read the inbox: the same notification gets the subject, without alerting again.
        fake.down = false
        fake.conversations = listOf(mail)
        assertTrue(pollNewMail(g, NotifyMode.IMPORTANT))
        val again = shown("c3075", 1)!!
        assertEquals("Budget for Q3", text(again, Notification.EXTRA_TEXT))
        assertTrue(again.flags and Notification.FLAG_ONLY_ALERT_ONCE != 0)
    }

    @Test fun anUnencryptedMessageNeverShowsItsOwnText() = runBlocking {
        fake.down = true
        PushHandler.handle(g, PushEvent.NewMail(3075, 9, true, "Dana Lee", "New message"), trusted = false)
        assertNull(shown("c3075", 1))
        // Not important, and the setting is "Important only": nothing either.
        PushHandler.handle(g, PushEvent.NewMail(3076, 10, false, "Eli", "New message"), trusted = true)
        assertNull(shown("c3076", 1))
    }

    @Test fun anApprovalPostsTheApprovalsNotification() = runBlocking {
        fake.approvals = listOf(Approval(41, actor = "Claude Code", action = "mail.send",
            preview = ApprovalPreview("Send “Re: Q3”", listOf("To dana@example.org"))))
        PushHandler.handle(g, PushEvent.Approval(41, null, "mail.send", "Approval request from Claude Code", "Send"), trusted = true)
        val n = shown(Notifier.approvalTag("41"), Notifier.APPROVAL_ID)!!
        assertEquals("Send “Re: Q3”", text(n, Notification.EXTRA_TITLE))
        assertEquals(Channels.APPROVALS, n.channelId)
        assertEquals(listOf("Allow once", "Deny"), n.actions.map { it.title.toString() })
        assertEquals("mailr://approvals/41", shadowOf(n.contentIntent).savedIntent.dataString)
        // The next poll sees it as already notified.
        nm.cancelAll()
        assertTrue(io.github.murtazanasir.mailr.data.sync.pollApprovals(g))
        assertNull(shown(Notifier.approvalTag("41"), Notifier.APPROVAL_ID))
    }

    @Test fun anApprovalWhileTheServerIsDownUsesThePushText() = runBlocking {
        fake.down = true
        PushHandler.handle(g, PushEvent.Approval(null, 12, null, "Approve an agent's email", "To dana@example.org. New recipient"), trusted = true)
        val n = shown(Notifier.approvalTag("o12"), Notifier.APPROVAL_ID)!!
        assertEquals("Approve an agent's email", text(n, Notification.EXTRA_TITLE))
        assertEquals("To dana@example.org. New recipient", text(n, Notification.EXTRA_TEXT))
        assertEquals(listOf("Allow once", "Deny"), n.actions.map { it.title.toString() })
        assertEquals("mailr://approvals/o12", shadowOf(n.contentIntent).savedIntent.dataString)
    }

    @Test fun aFailedSendIsNotifiedOnceAndOpensScheduled() = runBlocking {
        fake.failed = listOf(Outbox(12, "failed", recipients = listOf("dana@example.org"), error = "550 mailbox unavailable"))
        val e = PushEvent.SendFailed(12, "Message not sent", "To dana@example.org: 550 mailbox unavailable")
        PushHandler.handle(g, e, trusted = true)
        val n = shown(Notifier.sendFailedTag(12), Notifier.SEND_FAILED_ID)!!
        assertEquals("Message not sent", text(n, Notification.EXTRA_TITLE))
        assertEquals("To dana@example.org: 550 mailbox unavailable", text(n, Notification.EXTRA_TEXT))
        assertEquals(Channels.SENDING, n.channelId)
        assertEquals("mailr://scheduled", shadowOf(n.contentIntent).savedIntent.dataString)
        nm.cancelAll()
        PushHandler.handle(g, e, trusted = true)
        assertNull(shown(Notifier.sendFailedTag(12), Notifier.SEND_FAILED_ID))
        val link = parseDeepLink(android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse("mailr://scheduled")), internal = true)
        assertEquals(Destination.Scheduled, link)
    }

    @Test fun theTestMessageShows() = runBlocking {
        PushHandler.handle(g, PushEvent.Test("mailr", "Test notification"), trusted = true)
        val n = shown(Notifier.PUSH_TEST_TAG, Notifier.PUSH_TEST_ID)!!
        assertEquals("Test notification · push works", text(n, Notification.EXTRA_TEXT))
    }

    @Test fun inTheForegroundNothingIsPosted() = runBlocking {
        g.setForeground(true)
        fake.conversations = listOf(mail)
        fake.failed = listOf(Outbox(12, "failed", recipients = listOf("dana@example.org"), error = "550"))
        PushHandler.handle(g, PushEvent.NewMail(3075, 9, true, "Dana Lee", "New message"), trusted = true)
        PushHandler.handle(g, PushEvent.SendFailed(12, "Message not sent", "x"), trusted = true)
        assertEquals(0, shadowOf(nm).getAllNotifications().size)
    }

    /** The connector's keys in memory (Robolectric has no Android Keystore). */
    class MemoryKeys : KeyManager {
        private val made = mutableSetOf<String>()
        override fun decrypt(instance: String, sealed: ByteArray): ByteArray? = null
        override fun generate(instance: String) { made += instance }
        override fun getPublicKeySet(instance: String) = if (instance in made) PublicKeySet(PUB, AUTH) else null
        override fun exists(instance: String) = instance in made
        override fun delete(instance: String) { made -= instance }
    }

    companion object {
        const val NTFY = "io.heckel.ntfy"
        /** A VAPID public key as GET /push/targets gives it: an uncompressed P-256 point, base64url, unpadded. */
        val VAPID: String = Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(65) { if (it == 0) 4 else (it * 7).toByte() })
        const val EP1 = "https://ntfy.example.com/upAAAAAAAAAAAA?up=1"
        const val EP2 = "https://ntfy.example.com/upBBBBBBBBBBBB?up=1"
        const val PUB = "BByJdw7lLmFKXDRtDq0duG1FJYtG8jO9iZR0CEngA36jTWH4JVnhjxX9z6egV-sITAlt-Eg1mgXC9DZA7BbMN0w"
        const val AUTH = "AAECAwQFBgcICQoLDA0ODw"

        // A message encrypted by backend/mailr/core/webpush.encrypt for this key pair (generated once).
        const val V_PRIV = "rmMxjpULzITnazG5XxuH8w46_t0oGRaPcxJ4XfQH1r4"
        const val V_PUB = PUB
        const val V_AUTH = AUTH
        const val V_CT = "2pBXlEpZjf6jFUQmu3N_BQAAEABBBLZ_sDPYf2PJo0oD1rKMYn8VrU2NSso6CIDeK3i8L4O8WwU7lfdYz09yVk1SweolXv17SuRGFbSC81wSqXdDEtyhC8xZC-D4qXVwtxss0disw_jNByh2VNlXxgK4zdhhntgSwDkTtznDPGvQ7lOFjJmS8WbbOPVeIB-z6sidLS6fww_AZhNvYYp_cvpqYimkveToy4lBOf_Pz3gIKV-yvpzFOMs_-iIjtK0jG6FutImnmLinqthR6nLsY-18sRdYYoNUFhtJhqZ0fr2n6ZFNLw9RL1KPIzbxS-ucsQN70860eJ6BFxYj35Mt3ezmCPADtMVibmm0DfLZ6X3OFnL5zyeF0W-VFbyFpYvLuw"
    }
}
