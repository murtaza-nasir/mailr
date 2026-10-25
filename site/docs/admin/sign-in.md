# Sign-in and OIDC

Mailr has two ways to sign in: its own email and password, and single sign-on with any OIDC provider. Both can be enabled at once.

## Email and password

With `MAILR_LOCAL_LOGIN=true` (the default), people can sign in with email and password. Passwords must have at least 10 characters.

- With `MAILR_ALLOW_SIGNUP=true`, anyone who reaches the sign-in page can create an account. Leave it off (the default) and add users yourself. Sign-ups are limited per network address.
- Users change their own password under **Settings → You → Password & devices**. With a new password, every other session is signed out and every API token is revoked. Unless the user unticks **Also sign out mail and calendar apps**, the IMAP password gets a new secret that nobody is shown and the calendar apps' app passwords are deleted.
- Users who sign in with single sign-on use **Sign out everywhere** there: the same, without a password change.
- An admin resets a password under **Settings → Admin → Users**, or with `mailr set-password`. After a reset, the user's sessions are signed out, their API tokens are revoked, their push notification targets are removed, and their mail and calendar apps are signed out too. The same holds for a password set with `PATCH /api/v1/admin/users/{uid}`.
- Failed sign-ins are counted per network address, per address and account, and per account, and too many in 15 minutes get "too many attempts" for a while. The counts are shared across every server worker. A wrong email and a wrong password take the same time to answer.
- Each attempt is counted before its password is checked, so many guesses sent at the same moment cannot pass the limit together; a successful sign-in takes its count back. An IPv6 client is counted by its /64 network, since one host usually holds a whole /64. Passwords are checked in a worker thread, a few at a time, so sign-in attempts do not slow down the rest of the server.
- The per-account count does not lock a person out of a device they signed in from before: a browser that holds Mailr's sign-in cookie for the account, or a network address that has an open session of the account, has a count of its own (10 failures in 15 minutes). Strangers who fill the per-account count therefore do not lock the owner out of known devices. On a new device and a new network, the owner must still wait or sign in with single sign-on. A password change, an administrator's password reset and **Sign out everywhere** end this for every browser that signed in before: each one counts as a new device until it signs in again.

## Single sign-on (OIDC)

Pocket ID, Authentik, Keycloak, Authelia and any other OIDC provider are supported.

1. At your provider, create a client for Mailr:
    - **Redirect URI**: `${MAILR_BASE_URL}/api/v1/auth/oidc/callback`, for example `https://mail.example.com/api/v1/auth/oidc/callback`.
    - **Scopes**: `openid email profile`, plus `groups` if you use an admin group or [access policies](access.md).
    - Allow PKCE.
2. Set in `.env`:

    ```ini
    MAILR_OIDC_ISSUER=https://id.example.com
    MAILR_OIDC_CLIENT_ID=mailr
    MAILR_OIDC_CLIENT_SECRET=...          # optional for public clients with PKCE
    # MAILR_OIDC_NAME=Pocket ID           # the name on the sign-in buttons
    # MAILR_OIDC_SCOPES=openid email profile groups
    # MAILR_OIDC_ADMIN_GROUP=mailr-admins
    ```

3. Restart with `docker compose up -d`. A **Continue with single sign-on** button then appears on the sign-in page. With `MAILR_OIDC_NAME` set, the button names your provider instead (`MAILR_OIDC_NAME=Pocket ID` gives **Continue with Pocket ID**), on the web and in the phone apps.

Account matching:

- A person who already has a Mailr account is matched by email on their first single sign-on.
- By default, only people added by an admin can sign in. With `MAILR_OIDC_AUTO_CREATE=true`, a new person gets a Mailr account on their first sign-in. Set `MAILR_OIDC_AUTO_CREATE_GROUP` as well to give accounts only to members of one group at your identity provider. The owner of an account made this way can sign in only while they stay in that group: after removal from the group, the next single sign-on is refused. Accounts added by an admin are not affected; disable those under Users.
- An existing account is joined by email at single sign-on only when the address is verified at the identity provider. An account that someone created through self-service sign-up is never joined: nobody has proved that the address belongs to them. An administrator can mark the address as verified with `PATCH /api/v1/admin/users/{uid}` and `{"email_verified": true}`; the account is then joined at the next single sign-on.
- With `MAILR_OIDC_ADMIN_GROUP` set, members of that group are admins, and everyone else is a regular user. The group is checked at every sign-in, so people who leave the group lose admin rights the next time they sign in.

When single sign-on works, set `MAILR_LOCAL_LOGIN=false` to make it the only way in, before you expose Mailr to the internet.

### Profile pictures from the provider

When the ID token or the provider's userinfo endpoint has a `picture` claim, that picture is used as the person's account picture until they upload their own. An uploaded picture always has priority, and after **Remove** the provider's picture does not come back by itself. People choose **Use my sign-in picture** under **Settings → You → Profile → Your picture** to switch back.

- The picture is fetched on the server at sign-in, from the web or the Android app. It is fetched again only when the address changes or the copy is a day old.
- The fetch is https only, at most 2 MB, and only image files. The picture is re-encoded as a 256-pixel PNG without its metadata.
- Pictures are fetched only from the issuer's own host and port. The issuer may be on the LAN, because it is configured by the admin. A loopback address must still be listed in `MAILR_OUTBOUND_ALLOW`.
- A picture address on any other host is ignored. For a provider with pictures on a separate host, such as Google, add that host to `MAILR_OIDC_PICTURE_HOSTS`, for example `MAILR_OIDC_PICTURE_HOSTS=lh3.googleusercontent.com`. Hosts listed there must be on the public internet.
- When a sign-in is required for the picture address, the fetch is retried once with the access token of the web sign-in that is running, and only on the issuer's host. The token is never stored. Only the ID token is sent to Mailr from the Android app, so such a picture appears after the next web sign-in. No sign-in is required for pictures from Pocket ID.

## The Android app

The server's sign-in options are read when you enter the server address in the app.

- With `MAILR_OIDC_NATIVE_CLIENT_ID` set, a single sign-on button is shown in the app. Sign-in through the button takes place at the provider in a browser tab, followed by a return to the app.
- Without the variable, sign-in is with email and password.

To set it up, create a second, **public** client at your provider (no secret, PKCE required). Add two redirect URIs to it: `io.github.murtazanasir.mailr:/oauth2redirect` for the Mailr app and `io.github.murtazanasir.mailr.notes:/oauth2redirect` for the Notes app. These are the defaults of `MAILR_OIDC_NATIVE_REDIRECT` and `MAILR_OIDC_NATIVE_NOTES_REDIRECT`, and they match the published apps. Change them only when you build the apps with your own application id. Then set `MAILR_OIDC_NATIVE_CLIENT_ID` to the new client id. Several ids, comma separated, are allowed.

### Server-bound sign-in {#the-sign-in-is-bound-to-your-server}

The phone apps' sign-in is tied to the server address the app uses. A one-time value (the nonce), made from the server's address and a random value, is requested from the provider, and the random value is sent to Mailr with the provider's ID token. The same value is computed from Mailr's own address, `MAILR_BASE_URL`, and the sign-in is refused when the two differ. An ID token that was obtained while the app was connected to another server (a look-alike host from a planted link), or by another app with the same public client id, therefore cannot be used to sign in. Each ID token can still be used only once.

- **Other addresses of the server.** When phones reach Mailr through another address than `MAILR_BASE_URL`, for example a local network address, list it in `MAILR_OIDC_NATIVE_ORIGINS` (comma separated, such as `http://192.0.2.20:8025`). Otherwise sign-in through that address is refused with "this sign-in was started for another server".
- **Older app versions.** App versions without the server-bound sign-in send no such value. They are asked to update the app ("this version of the app can no longer sign in"); people who are already signed in stay signed in. While phones are updated, you can set `MAILR_OIDC_NATIVE_UNBOUND=true` to let those versions sign in as before. Turn it off again as soon as the phones are updated: while it is on, the protection above does not apply to them.

## Sessions

A sign-in lasts `MAILR_SESSION_DAYS` days (30 by default). Each user's browsers and phones are listed under **Settings → You → Password & devices**, where any of them can be signed out. A session stays valid until it expires or is signed out.

API tokens for agents are separate from sessions. See [Tokens and scopes](../agents/tokens.md).
