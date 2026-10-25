package io.github.murtazanasir.mailr.ui.assistant

import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.api.sourceRows
import io.github.murtazanasir.mailr.i18n.AppText
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * A slash command of the chat (backend/mailr/agent/commands.py, GET /agent/commands), in the user's
 * language. [kind]: `app` (the app runs it: AssistantViewModel.runCommand), `info` (the server answers
 * it at once, `/model`) or `ask` (the server turns it into a request to the assistant).
 * [arg]: `none`, `optional` or `required`.
 */
@Serializable
data class ChatCommand(
    val name: String,
    val aliases: List<String> = emptyList(),
    val kind: String = "ask",
    val group: String = "ask",
    val arg: String = "none",
    val summary: String = "",
    val example: String? = null,
)

/** A skill of the user's (GET /assistant/skills): `/name` adds its instructions to the question. */
@Serializable
data class SkillLite(
    val name: String,
    val title: String = "",
    val description: String = "",
    val enabled: Boolean = true,
    val auto: Boolean = false,
)

@Serializable
internal data class CommandList(val items: List<ChatCommand> = emptyList())

@Serializable
internal data class SkillList(val items: List<SkillLite> = emptyList())

/**
 * A row of the message box's picker: a command or a skill, its name and one line on what it does.
 * [complete]: a command that takes no text, typed in full; a tap sends it (as Enter does on the web).
 */
data class PickItem(val command: Boolean, val name: String, val desc: String, val complete: Boolean = false)

/**
 * The chat's slash commands, as the web has them (frontend/src/features/rail/assistant/commands.ts
 * and SkillPicker.tsx). A command opens the message (`/today`, `/task call the bank fri 3pm`); a skill
 * is `/name` too, but a command comes first. `//name` lists the skills only.
 */
object ChatCommands {
    private val LEADING = Regex("""^/([A-Za-z][A-Za-z0-9_-]*)(?:\s+([\s\S]*))?$""")
    /** The `/prefix` the message box holds while the picker is open: nothing else typed yet. */
    private val TOKEN = Regex("""^(//?)([A-Za-z0-9_-]*)$""")

    /** The command a message starts with and the text after it (as the server reads it), or null. */
    fun parse(text: String, list: List<ChatCommand>): Pair<ChatCommand, String>? {
        val m = LEADING.matchEntire(text.trim()) ?: return null
        val name = m.groupValues[1].lowercase()
        val cmd = list.firstOrNull { it.name == name || name in it.aliases } ?: return null
        return cmd to m.groupValues[2].trim()
    }

    /** The commands whose name or alias starts with [prefix], in list order. */
    fun match(list: List<ChatCommand>, prefix: String): List<ChatCommand> =
        list.filter { c -> c.name.startsWith(prefix) || c.aliases.any { it.startsWith(prefix) } }

    /**
     * The picker's rows for the message box's [text]: the matching commands, then the user's skills
     * (enabled, not automatic), at most 12 (8 for skills alone). Empty when the box does not hold a
     * leading `/` and a name being typed: the picker is then closed.
     */
    fun picker(text: String, commands: List<ChatCommand>, skills: List<SkillLite>): List<PickItem> {
        val m = TOKEN.matchEntire(text) ?: return emptyList()
        val withCommands = m.groupValues[1] == "/"
        val prefix = m.groupValues[2].lowercase()
        val cmds = if (!withCommands) emptyList() else match(commands, prefix).map { c ->
            PickItem(true, c.name, c.summary, complete = c.arg == "none" && (c.name == prefix || prefix in c.aliases))
        }
        val sk = skills.filter { it.enabled && !it.auto && it.name.startsWith(prefix) }
            .map { PickItem(false, it.name, it.description.ifBlank { it.title }) }
        return (cmds + sk).take(if (withCommands) 12 else 8)
    }

    /** What a tap (or Send while the picker is open) puts in the box: `/name ` and a space for the text. */
    fun insert(item: PickItem): String = "/${item.name} "

    /** `/help`: the commands by group, then the user's skills (Markdown, shown as an answer). */
    fun helpText(list: List<ChatCommand>, skills: List<SkillLite>): String {
        val groups = listOf("chat" to R.string.asst_cmd_group_chat, "add" to R.string.asst_cmd_group_add, "ask" to R.string.asst_cmd_group_ask)
        val parts = groups.map { (g, head) ->
            val rows = list.filter { it.group == g }.joinToString("\n") { c ->
                val aliases = if (c.aliases.isEmpty()) "" else " (${c.aliases.joinToString(", ") { "`/$it`" }})"
                "- `/${c.name}`$aliases — ${c.summary}"
            }
            "**${AppText.get(head)}**\n$rows"
        }.toMutableList()
        val mine = skills.filter { it.enabled && !it.auto }
        if (mine.isNotEmpty()) parts += "**${AppText.get(R.string.asst_cmd_group_skills)}**\n" +
            mine.joinToString("\n") { "- `/${it.name}` — ${it.description.ifBlank { it.title }}" }
        parts += AppText.get(R.string.asst_cmd_help_hint)
        return parts.joinToString("\n\n")
    }

    private val CITE = Regex("""\[(\d+)]\(mailr-cite:\d+\)""")

    /** `/copy`: the answer's text with its citations as `[n]`, and one line per source it uses (the web's copyText). */
    fun copyText(md: String, citations: List<Citation>): String {
        val used = mutableSetOf<Int>()
        val body = CITE.replace(md) { m -> m.groupValues[1].also { used += it.toInt() }.let { "[$it]" } }.trim()
        val src = sourceRows(citations.filter { it.n in used }).map { row ->
            val c = row.first
            val subject = c.subject?.ifBlank { null } ?: c.title?.ifBlank { null } ?: AppText.get(R.string.asst_no_subject)
            val from = c.from?.let { " — $it" }.orEmpty()
            val date = c.date?.take(10)?.let { " ($it)" }.orEmpty()
            row.all.joinToString("") { "[${it.n}]" } + " $subject$from$date"
        }
        return if (src.isEmpty()) body else AppText.get(R.string.asst_cmd_copy_sources, body, src.joinToString("\n"))
    }

    private val LINK = Regex("""\[([^\]]+)]\([^)]*\)""")
    private val MARKS = Regex("""^#+\s*|^[-*>]\s+|[*_`]""")
    private val RULE = Regex("""^[-=*_#>\s]+$""")
    private const val BODY_MAX = 20_000

    /** `/note`: the first line of the answer as plain words, for the note's title (the web's answerTitle). */
    fun answerTitle(md: String): String {
        val line = md.split("\n").map { it.trim() }.firstOrNull { it.isNotEmpty() && !RULE.matches(it) } ?: ""
        // A citation removed before a full stop leaves no space behind ("accountant [1]." -> "accountant.").
        val plain = MARKS.replace(LINK.replace(CITE.replace(line, ""), "$1"), "").replace(Regex("""\s+"""), " ")
            .replace(Regex(""" ([.,;:!?)])"""), "$1").trim()
        return if (plain.length > 80) plain.take(79).trimEnd() + "…" else plain
    }

    /**
     * `/note`: the note an answer becomes (the web's answerNote in notes/fromAnswer.ts): its text with
     * the sources it uses, as `/copy` has it, and a link (POST /notes/{id}/links) per cited email,
     * event, task or recording. [fallbackTitle] names a note whose answer has no words on its first line.
     */
    fun answerNote(md: String, citations: List<Citation>, fallbackTitle: String): AnswerNote {
        val body = copyText(md, citations).take(BODY_MAX)
        val used = CITE.findAll(md).map { it.groupValues[1].toInt() }.toSet()
        val seen = mutableSetOf<String>()
        val links = mutableListOf<JsonObject>()
        fun add(kind: String, key: String, ref: JsonObject) {
            if (seen.add(key)) links += buildJsonObject { put("target_kind", kind); put("target_ref", ref) }
        }
        for (c in citations.filter { it.n in used }.sortedBy { it.n }) {
            val uid = c.uid
            when {
                (c.kind == "message" || c.kind == "conversation") && c.conversationId != null ->
                    add("conversation", "c:${c.conversationId}", buildJsonObject { put("conversation_id", c.conversationId) })
                c.kind == "event" && !uid.isNullOrEmpty() -> add("event", "e:$uid", buildJsonObject {
                    put("uid", uid)
                    c.occurrence?.takeIf { it.isNotEmpty() }?.let { put("occurrence", it) }
                })
                c.kind == "task" && !uid.isNullOrEmpty() -> add("task", "t:$uid", buildJsonObject { put("uid", uid) })
                c.kind == "recording" && c.connectorId != null && c.recordingId != null ->
                    add("recording", "r:${c.connectorId}:${c.recordingId}", buildJsonObject {
                        put("connector_id", c.connectorId); put("recording_id", c.recordingId)
                    })
            }
        }
        return AnswerNote(answerTitle(md).ifEmpty { fallbackTitle }, body, links)
    }
}

/** A text note made from an answer (`/note`): its title, body and the links to add after it is made. */
data class AnswerNote(val title: String, val body: String, val links: List<JsonObject>)
