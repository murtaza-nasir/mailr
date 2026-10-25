package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.data.api.AttachmentPreview
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.Participant
import io.github.murtazanasir.mailr.data.api.PrimaryAction
import io.github.murtazanasir.mailr.data.api.Signal
import io.github.murtazanasir.mailr.data.repo.MailView
import java.time.ZonedDateTime

/** List rows with labels, notes to self and the Later tab (RowScreenshotTest, ConversationRowTest). */
object RowFixtures {
    private val now: ZonedDateTime = AppClock.now()
    private fun ago(hours: Long) = now.minusHours(hours).toOffsetDateTime().toString()
    private val me = Participant("me@example.com", "Alex Morgan", me = true)

    val labels = Fixtures.labels + listOf(
        Label(20, "Later", color = "#f9ab00"),
        Label(21, "Projects", color = "#1a73e8"),
        Label(22, "Teaching/CHEM 101", color = "#188038"),
        Label(23, "Receipts"),
    )

    /** The default layout's Later tab: one section on the Later label. */
    val laterView = MailView("inbox", title = "Later", layout = "default", tab = "later")

    val inbox = listOf(
        ConversationSummary(1, "Module 6 deck review before Thursday", "I went through the slides and left comments on the Streamlit part.",
            ago(1), 3, 1, participants = listOf(Participant("dana@example.edu", "Dana Whitfield"), me),
            labelIds = listOf(1, 21, 22, 10), signals = listOf(Signal("needs_reply"))),
        ConversationSummary(2, "Your order has shipped", "Arriving today 1:30–4:30 PM. Track your package for live updates.",
            ago(3), 1, 0, participants = listOf(Participant("ship@example.com", "Acme Store")), labelIds = listOf(1, 23),
            primaryAction = PrimaryAction("track", "Track package", "https://example.com/t", fact = "Arriving today")),
        ConversationSummary(3, "PADS Invites Submissions for 2027 Conference",
            "PADS 2027 CALL FOR PAPERS. As Program Chair for the 2027 Prairie Analytics and Decision Society, I invite you to submit.",
            ago(30), 2, 0, starred = true, important = true,
            participants = listOf(Participant("info@pads.example", "Prairie Analytics and Decision Society")),
            labelIds = listOf(1, 11), attachmentsPreview = listOf(AttachmentPreview("PADS_2027_CFP.pdf", "application/pdf"),
                AttachmentPreview("NAI_Logo_Final.png", "image/png")),
            signals = listOf(Signal("deadline", date = now.plusDays(1).toLocalDate().toString()))),
        ConversationSummary(4, "Projects", "", ago(50), 1, 0, participants = listOf(Participant("bob@example.com", "Bob Rivera")),
            labelIds = listOf(1, 21)),
        ConversationSummary(5, "Re: Quarterly plan", "Added my comments in the doc. Can we review on Friday?",
            ago(24 * 6), 4, 0, draftCount = 1, nudgedAt = ago(2),
            participants = listOf(Participant("alice@example.com", "Alice Chen"), me), labelIds = listOf(1)),
    )

    val later = listOf(
        ConversationSummary(11, "Screenshot (Oct 1, 2026 15:49:33)", "Screenshot (Oct 1, 2026 15:49:33)", ago(0), 1, 1,
            participants = listOf(me), toSelf = true, labelIds = listOf(1, 20),
            attachmentsPreview = listOf(AttachmentPreview("Screenshot_20261001_154933.png", "image/png"))),
        ConversationSummary(12, "mailr", "", ago(2), 1, 0, participants = listOf(me), toSelf = true, labelIds = listOf(1, 20)),
        ConversationSummary(13, "Read later: vLLM prefix caching notes",
            "Read later: vLLM prefix caching notes. https://docs.vllm.ai/en/latest/features/automatic_prefix_caching.html",
            ago(20), 1, 0, participants = listOf(me), toSelf = true, labelIds = listOf(1, 20, 21)),
        ConversationSummary(14, "Receipt for the HDMI cable", "", ago(26), 1, 0, participants = listOf(me), toSelf = true, labelIds = listOf(1, 20, 23),
            attachmentsPreview = listOf(AttachmentPreview("receipt.pdf", "application/pdf"), AttachmentPreview("photo.jpg", "image/jpeg"))),
        ConversationSummary(15, "Ask Dana about the M8 rubric", "Before Friday. She had the older version with the four criteria.",
            ago(48), 1, 0, participants = listOf(me), toSelf = true, labelIds = listOf(1, 20)),
    )
}
