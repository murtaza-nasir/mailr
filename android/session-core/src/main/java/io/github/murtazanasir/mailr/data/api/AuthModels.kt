package io.github.murtazanasir.mailr.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// The sign-in models, shared by the mailr app and the Notes app (android/session-core).

@Serializable
data class User(val id: Long, val email: String, val name: String = "", val role: String = "user")

@Serializable
data class AuthConfig(
    @SerialName("local_login") val localLogin: Boolean = true,
    val oidc: Boolean = false,
    @SerialName("oidc_name") val oidcName: String? = null,
)

@Serializable
data class NativeConfig(
    val enabled: Boolean = false,
    val issuer: String = "",
    @SerialName("client_id") val clientId: String = "",
    @SerialName("redirect_uri") val redirectUri: String = "",
    val scopes: List<String> = listOf("openid", "email", "profile"),
    @SerialName("local_login") val localLogin: Boolean = true,
    /** The provider's name for the sign-in button (MAILR_OIDC_NAME); empty = "single sign-on". */
    @SerialName("provider_name") val providerName: String = "",
)

@Serializable
data class SessionResponse(
    @SerialName("session_token") val sessionToken: String? = null,
    val csrf: String? = null,
    val user: User,
    @SerialName("expires_at") val expiresAt: String? = null,
)

/** A sign-in hand-over token (`POST /auth/handover`): single use, [expiresIn] seconds. */
@Serializable
data class HandoverToken(val token: String, @SerialName("expires_in") val expiresIn: Int = 120)
