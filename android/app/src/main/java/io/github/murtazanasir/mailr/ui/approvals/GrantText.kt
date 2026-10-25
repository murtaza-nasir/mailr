package io.github.murtazanasir.mailr.ui.approvals

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.Approval

/**
 * The words of the grants on an approval card. A grant ("Allow
 * for 12 hours", "Always allow") also decides the other waiting requests its rule covers. The card
 * says how many before the tap (the server's also_covers), and the snackbar after it, with Undo.
 * The same words as the web (frontend/src/features/approvals/grant.ts).
 */
object GrantText {
    /** What a grant on each class lets the agent do, in plain words. */
    private val VERBS = mapOf(
        "mail.send" to R.string.appr_verb_mail_send,
        "mail.trash" to R.string.appr_verb_mail_trash,
        "mail.spam" to R.string.appr_verb_mail_spam,
        "mail.delete" to R.string.appr_verb_mail_delete,
        "mail.label" to R.string.appr_verb_mail_label,
        "mail.unsubscribe" to R.string.appr_verb_mail_unsubscribe,
        "rules.write" to R.string.appr_verb_rules_write,
        "calendar.create" to R.string.appr_verb_calendar_create,
        "calendar.update" to R.string.appr_verb_calendar_update,
        "calendar.delete" to R.string.appr_verb_calendar_delete,
        "calendar.rsvp" to R.string.appr_verb_calendar_rsvp,
        "calendar.invite" to R.string.appr_verb_calendar_invite,
        "calendar.cancel" to R.string.appr_verb_calendar_cancel,
        "tasks.create" to R.string.appr_verb_tasks_create,
        "tasks.update" to R.string.appr_verb_tasks_update,
        "tasks.complete" to R.string.appr_verb_tasks_complete,
        "tasks.delete" to R.string.appr_verb_tasks_delete,
        "labels.write" to R.string.appr_verb_labels_write,
        "labels.delete" to R.string.appr_verb_labels_delete,
        "contacts.write" to R.string.appr_verb_contacts_write,
        "memory.write" to R.string.appr_verb_memory_write,
        "prefs.write" to R.string.appr_verb_prefs_write
    )

    fun verb(action: String) = VERBS[action]?.let { AppText.get(it) } ?: action

    private fun forToken(a: Approval) = a.subject.startsWith("token:")

    /** "Allow for 12 hours" for an agent token, "Allow for this chat" for the assistant. */
    fun sessionLabel(a: Approval): String = AppText.get(if (forToken(a)) R.string.appr_allow_12h else R.string.appr_allow_chat)

    /** " (also approves 2 other waiting)" on a grant; empty when it covers no other request. */
    fun also(n: Int?): String = if (n != null && n > 0) AppText.plural(R.plurals.appr_also_covers, n, n) else ""

    /** The snackbar after a grant: "Allowed for 12 hours. Also approved 2 other requests." */
    fun granted(scope: String, sessionLabel: String, n: Int): String {
        val first = when {
            scope != "session" -> AppText.get(R.string.appr_rule_created)
            sessionLabel == AppText.get(R.string.appr_allow_12h) -> AppText.get(R.string.appr_allowed_12h)
            sessionLabel == AppText.get(R.string.appr_allow_chat) -> AppText.get(R.string.appr_allowed_chat)
            else -> "$sessionLabel."
        }
        return AppText.plural(R.plurals.appr_also_approved, n, first, n)
    }

    fun denied(n: Int) = AppText.plural(R.plurals.appr_denied_also, n, n)

    /** The snackbar after Undo: what went back to waiting, and what had already run. */
    fun undone(reverted: Int, kept: Int): String {
        if (kept == 0) return AppText.plural(R.plurals.appr_undone_waiting, reverted, reverted)
        if (reverted == 0) return AppText.plural(R.plurals.appr_rule_removed_ran, kept, kept)
        return AppText.plural(R.plurals.appr_undone_for, reverted, reverted) + " " + AppText.plural(R.plurals.appr_already_ran, kept, kept)
    }

    /** What each grant covers, under its menu item. */
    fun sessionHelp(a: Approval): String =
        AppText.get(if (forToken(a)) R.string.appr_session_help_12h else R.string.appr_session_help_chat, verb(a.action))

    val ALWAYS_HELP: String get() = AppText.get(R.string.appr_always_help)
}
