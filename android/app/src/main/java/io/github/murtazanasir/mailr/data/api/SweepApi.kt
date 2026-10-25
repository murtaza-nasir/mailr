package io.github.murtazanasir.mailr.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

// Settings → Ambrose → Sweep (backend agent/review.py): the user's standing rules for every bulk review
// (the setting sweep_rules) and the moves remembered from review cards, per sender
// (GET/DELETE /agent/review-choices). Unknown fields are ignored.

/** A move remembered for one sender: trash, archive, read, keep or label:<id> (with its name). */
@Serializable
data class ReviewChoice(
    val id: Long = 0,
    val sender: String = "",
    val move: String = "keep",
    val label: String? = null,
    val count: Int = 1,
    @SerialName("updated_at") val updatedAt: String? = null,
)

@Serializable
data class ReviewChoices(val items: List<ReviewChoice> = emptyList())

/** The longest rules the server takes (agent/review.py RULES_MAX). */
const val SWEEP_RULES_MAX = 2000

private const val CHOICES = "agent/review-choices"

/** The saved rules (GET /me, settings.sweep_rules). */
suspend fun MailrApi.sweepRules(): String = me().settings["sweep_rules"]?.jsonPrimitive?.contentOrNull.orEmpty()

// ACT settings.sweep_rules @android.settings
suspend fun MailrApi.setSweepRules(text: String): JsonObject =
    patchSettings(JsonObject(mapOf("sweep_rules" to JsonPrimitive(text.trim().take(SWEEP_RULES_MAX)))))

suspend fun MailrApi.reviewChoices(): ReviewChoices = client.get(CHOICES, ReviewChoices.serializer())

// ACT agent.review_choice_remove @android.settings
suspend fun MailrApi.removeReviewChoice(id: Long): JsonObject = client.send("DELETE", "$CHOICES/$id", JsonObject.serializer())

// ACT agent.review_choice_clear @android.settings
suspend fun MailrApi.clearReviewChoices(): JsonObject = client.send("DELETE", CHOICES, JsonObject.serializer())
