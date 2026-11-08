package io.github.murtazanasir.mailr.ui.approvals

import io.github.murtazanasir.mailr.data.api.AlsoCovers
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.ApprovalHead
import io.github.murtazanasir.mailr.data.api.ApprovalPreview
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.present.Display
import io.github.murtazanasir.mailr.ui.present.DisplayField
import io.github.murtazanasir.mailr.ui.present.DisplayValue

/** Neutral approval cards in the server's shape (agent/present.py card_head): tasks from one token, and an email. */
object CardSamples {
    private val now get() = AppClock.now()
    const val LONG_TITLE = "Research workshop: dry run in the seminar room with the team (check projector, outlets and the HDMI cable)"
    const val TAINTED = "An agent wrote this text after reading email (or through an API token), so it asks first"

    private fun f(label: String, text: String, t: String = "text") = DisplayField(label, DisplayValue(t, text))

    fun task(id: Long, name: String, due: String, minutesAgo: Long): Approval = Approval(
        id, "token:7", "claude-code", "tasks.create", "Calendar", ApprovalPreview("Add task “${name.take(80)}”"),
        createdAt = now.minusMinutes(minutesAgo).toOffsetDateTime().toString(),
        expiresAt = now.plusHours(23).toOffsetDateTime().toString(),
        display = Display(icon = "task", title = "Add task “${name.take(80)}”", reason = TAINTED,
            fields = listOf(f("Due", due, "when"), f("List", "Calendar"))),
        alsoCovers = AlsoCovers(session = 1, always = 1, alwaysDerived = 1),
        head = ApprovalHead("task_new", "New task", name = name, reasonShort = "Asks first: written through an API token"))

    /** Two tasks from claude-code, newest first: one group. */
    val tasks = listOf(
        task(81, "Update the workshop RSVP form", "Thu 5 Nov, 09:00", 58),
        task(80, LONG_TITLE, "Mon 30 Nov, 09:00", 60),
    )

    val email: Approval = Approval(
        90, "session:s1", "Ambrose", "mail.send", "alice@example.com", ApprovalPreview("Send to Alice Chen"),
        createdAt = now.minusMinutes(3).toOffsetDateTime().toString(), chatSessionId = "s1",
        display = Display(icon = "send", title = "Send to Alice Chen", fields = listOf(
            f("From", "me@example.com"),
            DisplayField("To", DisplayValue("people", "Alice Chen <alice@example.com>",
                items = MailrJson.parseToJsonElement("""[{"name":"Alice Chen","email":"alice@example.com"}]""") as kotlinx.serialization.json.JsonArray)),
            f("Subject", "Workshop room on Thursday"),
            f("", "Hi Alice,\nThe seminar room is booked from 2 to 4 PM. I will bring the projector cable.\nSee you then!", "quote"))),
        alsoCovers = AlsoCovers(),
        head = ApprovalHead("send", "Send email", name = "Workshop room on Thursday"))
}
