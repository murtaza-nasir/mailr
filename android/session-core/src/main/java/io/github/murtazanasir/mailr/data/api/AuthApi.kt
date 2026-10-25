package io.github.murtazanasir.mailr.data.api

import io.github.murtazanasir.mailr.util.catching

import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * The sign-in routes. [app] names the app asking for the native
 * configuration: null for the mailr app, "notes" for the Notes app, which has its own OIDC redirect.
 */
class AuthApi(val client: ApiClient, private val app: String? = null) {
    suspend fun authConfig() = client.get("auth/config", AuthConfig.serializer())

    suspend fun nativeConfig() = client.get("auth/native-config", NativeConfig.serializer(),
        app?.let { mapOf("app" to it) }.orEmpty())

    /** [invite]: a household invite the app was set up from; with it single sign-on may make the invited account. */
    /** [nonceSeed]: the seed of the request's server-bound nonce (data/auth/NonceBinding.kt). */
    suspend fun nativeSignIn(idToken: String, device: String, invite: String? = null, nonceSeed: String? = null) = client.send(
        "POST", "auth/oidc/native", SessionResponse.serializer(),
        MailrJson.encodeToString(buildJsonObject {
            put("id_token", idToken); put("device_name", device); invite?.let { put("invite", it) }
            nonceSeed?.let { put("nonce_seed", it) }
        }),
        idempotencyKey = null,
    )

    suspend fun passwordSignIn(email: String, password: String) = client.send(
        "POST", "auth/login", SessionResponse.serializer(),
        MailrJson.encodeToString(buildJsonObject { put("email", email); put("password", password) }),
        idempotencyKey = null,
    )

    /** The mailr app's side of "Continue as" (data/auth/Handover.kt): a single-use token for [app]. */
    suspend fun mintHandover(app: String, challenge: String, device: String) = client.send(
        "POST", "auth/handover", HandoverToken.serializer(),
        MailrJson.encodeToString(buildJsonObject { put("app", app); put("challenge", challenge); put("device_name", device) }),
        idempotencyKey = null,
    )

    /** The Notes app's side: the token and the verifier for a device session of its own. */
    suspend fun redeemHandover(token: String, verifier: String, device: String) = client.send(
        "POST", "auth/handover/redeem", SessionResponse.serializer(),
        MailrJson.encodeToString(buildJsonObject { put("token", token); put("verifier", verifier); put("device_name", device) }),
        idempotencyKey = null,
    )

    suspend fun signOut() = catching {
        client.send("POST", "auth/native/logout", JsonObject.serializer(), idempotencyKey = null)
    }
}
