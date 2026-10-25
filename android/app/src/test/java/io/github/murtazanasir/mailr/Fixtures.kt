package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.ApprovalPreview
import io.github.murtazanasir.mailr.data.api.Attachment
import io.github.murtazanasir.mailr.data.api.AttachmentPreview
import io.github.murtazanasir.mailr.data.api.Attendee
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.api.ConversationDetail
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.Message
import io.github.murtazanasir.mailr.data.api.Participant
import io.github.murtazanasir.mailr.data.api.TaskItem
import java.time.ZonedDateTime

/** Realistic sample data for screenshot and unit tests. */
object Fixtures {
    private val now: ZonedDateTime = AppClock.now()
    private fun ago(hours: Long) = now.minusHours(hours).toOffsetDateTime().toString()

    val labels = listOf(
        Label(1, "Inbox", "inbox", unread = 7, total = 42),
        Label(2, "Sent", "sent"), Label(3, "Drafts", "drafts", total = 2), Label(4, "Spam", "junk"),
        Label(5, "Trash", "trash"), Label(6, "Archive", "archive"),
        Label(10, "Research", color = "#1a73e8", unread = 2),
        Label(11, "Research/PADS 2027", color = "#188038"),
        Label(12, "Job Applications", color = "#e37400"),
        Label(13, "Travel", color = "#a142f4"),
    )

    val conversations = listOf(
        ConversationSummary(3075, "竹村鷹基 just messaged you", "1 new message awaits your response on LinkedIn",
            ago(2), 1, 1, important = true, participants = listOf(Participant("messages@linkedin.com", "竹村鷹基 via LinkedIn")),
            labelIds = listOf(1)),
        ConversationSummary(3066, "PADS Invites Submissions for 2027 Conference",
            "PADS 2027 CALL FOR PAPERS — As Program Chair for the 2027 Prairie Analytics and Decision Society, I invite you…",
            ago(30), 2, 2, important = true, starred = true,
            participants = listOf(Participant("info@pads.example", "Northfield Analytics"), Participant("me@example.com", "me", me = true)),
            labelIds = listOf(1, 11), attachmentsPreview = listOf(AttachmentPreview("NAI_Logo_Final.png", "image/png"))),
        ConversationSummary(3074, "maestro: your repo has a hardcoded fallback secret",
            "Hi, I run a free security scanner and it flagged something in maestro_backend/setup_first_user.py…",
            ago(72), 1, 1, participants = listOf(Participant("prbl@example.com", "Prbl")), labelIds = listOf(1, 10)),
        ConversationSummary(3070, "[speakr] Feature Request: Archive recordings", "setup562 created an issue: please add an option to archive recordings",
            ago(96), 3, 0, participants = listOf(Participant("a@github.com", "setup562"), Participant("b@github.com", "Mike"),
                Participant("me@example.com", "me", me = true)), labelIds = listOf(1), category = "updates"),
        ConversationSummary(3060, "Your booking confirmation: Park Lane Hotel", "Thanks for booking with Super.com. Your reservation is confirmed for Oct 12–14.",
            ago(24 * 9), 1, 0, participants = listOf(Participant("noreply@super.com", "Super.com")), labelIds = listOf(1, 13),
            attachmentsPreview = listOf(AttachmentPreview("itinerary.pdf", "application/pdf"))),
        ConversationSummary(3050, "Re: Quarterly plan", "Added my comments in the doc. Can we review on Friday?",
            ago(24 * 40), 4, 0, draftCount = 1, participants = listOf(Participant("alice@example.com", "Alice Chen"),
                Participant("me@example.com", "me", me = true), Participant("bob@example.com", "Bob Rivera")), labelIds = listOf(1)),
    )

    val detail = ConversationDetail(
        conversations[1],
        listOf(
            Message(501, 3066, Address("info@pads.example", "Northfield Analytics Institute"), listOf(Address("me@example.com", "Alex")),
                subject = "PADS Invites Submissions for 2027 Conference", date = ago(60),
                snippet = "PADS 2027 CALL FOR PAPERS — As Program Chair…", seen = true),
            Message(502, 3066, Address("chair@pads.example", "Program Chair"), listOf(Address("me@example.com", "Alex")),
                cc = listOf(Address("board@pads.example", "PADS Board")),
                subject = "Re: PADS Invites Submissions for 2027 Conference", date = ago(30),
                snippet = "Friendly reminder: award nominations close Sep 30.", seen = false, flagged = true,
                attachments = listOf(Attachment(2, "PADS_2027_CFP.pdf", "application/pdf", 482_000),
                    Attachment(3, "NAI_Logo_Final.png", "image/png", 91_000)),
                listUnsubscribe = "<https://pads.example/unsub>"),
        ),
    )

    val approvals = listOf(
        Approval(41, "token:12", "Claude Code", "calendar.create", "Default Calendar",
            ApprovalPreview("Create event", listOf("Wed 30 Sep, 14:00–15:00", "Review PADS submission with Alice")),
            expiresAt = now.plusHours(20).toOffsetDateTime().toString()),
        Approval(42, "assistant", "Ambrose", "mail.send", "alice@example.com",
            ApprovalPreview("Send “Re: Quarterly plan”", listOf("To Alice Chen <alice@example.com>", "Friday at 10 works for me. See you then!"))),
    )

    /** Recent decisions for the Approvals screen's history. */
    val approvalHistory = listOf(
        io.github.murtazanasir.mailr.ui.approvals.ApprovalRecord(40, "Claude Code", "tasks.create", "Inbox",
            ApprovalPreview("Add task", listOf("Grade project drafts")), "executed",
            decidedAt = now.minusMinutes(25).toOffsetDateTime().toString(), executedAt = now.minusMinutes(25).toOffsetDateTime().toString()),
        io.github.murtazanasir.mailr.ui.approvals.ApprovalRecord(39, "Ambrose", "mail.send", "bob@example.com",
            ApprovalPreview("Send “Lunch on Friday?”"), "denied", decidedAt = now.minusHours(3).toOffsetDateTime().toString()),
        io.github.murtazanasir.mailr.ui.approvals.ApprovalRecord(38, "Claude Code", "calendar.update", "Default Calendar",
            ApprovalPreview("Move “Office hours”"), "failed", decidedAt = now.minusDays(1).toOffsetDateTime().toString(),
            result = kotlinx.serialization.json.buildJsonObject { put("error", kotlinx.serialization.json.JsonPrimitive("The event was deleted on the server.")) }),
        io.github.murtazanasir.mailr.ui.approvals.ApprovalRecord(37, "Claude Code", "mail.label", "", ApprovalPreview("Label 3 conversations"),
            "expired", decidedAt = now.minusDays(2).toOffsetDateTime().toString()),
    )

    val events = listOf(
        CalendarEvent("e1", "CHEM 101 lecture", now.withHour(10).withMinute(0).toOffsetDateTime().toString(),
            now.withHour(11).withMinute(15).toOffsetDateTime().toString(), location = "Hollis Hall 208", color = "#1a73e8",
            reminders = listOf(10), recurring = true, rrule = "FREQ=WEEKLY;BYDAY=TU,TH"),
        CalendarEvent("e2", "Review with Alice", now.withHour(14).toOffsetDateTime().toString(), now.withHour(15).toOffsetDateTime().toString(),
            color = "#188038", organizer = "alice@example.com", organizerName = "Alice Chen", myStatus = "NEEDS-ACTION",
            attendeeList = listOf(Attendee("alice@example.com", "Alice Chen", "ACCEPTED"), Attendee("me@example.com", "Me", "NEEDS-ACTION"))),
        CalendarEvent("e4", "Office hours", now.withHour(14).withMinute(30).toOffsetDateTime().toString(),
            now.withHour(16).toOffsetDateTime().toString(), color = "#8e24aa"),
        CalendarEvent("e3", "PADS nominations due", now.plusDays(2).toLocalDate().toString(), now.plusDays(3).toLocalDate().toString(),
            allDay = true, color = "#e37400"),
    )

    val tasks = listOf(
        TaskItem("t1", "Reply to PADS program chair", now.plusDays(1).toLocalDate().toString(), priority = 1, reminders = listOf(-540)),
        TaskItem("t2", "Remove fallback secret from maestro", null, notes = "Rotate the key first"),
        TaskItem("t4", "Book hotel", null, parent = "t5"),
        TaskItem("t5", "Plan the Denver trip", now.withHour(17).withMinute(0).toOffsetDateTime().toString(), priority = 5),
        TaskItem("t6", "Grade project drafts", now.minusDays(1).toLocalDate().toString()),
        TaskItem("t3", "Book Park Lane parking", now.minusDays(1).toOffsetDateTime().toString(), status = "completed"),
    )

    val citations = listOf(
        Citation(1, "message", 3065, 499, "SPADS 2027 award nominations", "SPADS", ago(90), "nominations close Sep 30", true),
        Citation(2, "message", 3074, 510, "maestro: your repo has a hardcoded fallback secret", "Prbl", ago(72),
            "remove the fallback and throw at startup", true),
    )
}
