package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.data.api.AccountPicture
import io.github.murtazanasir.mailr.data.api.Identity
import io.github.murtazanasir.mailr.data.api.Me
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.User
import io.github.murtazanasir.mailr.ui.components.OwnPicture
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Which picture shows for you: the address's own, else the account picture, else initials. */
class OwnPictureTest {
    private val user = User(1, "me@example.com", "Me")
    private val account = AccountPicture(has = true, source = "upload", version = 42)

    private fun me(vararg ids: Identity, picture: AccountPicture? = account) = Me(user = user, identities = ids.toList(), picture = picture)

    @Test fun identityPictureWins() {
        val m = me(Identity(7, "me@example.com", isDefault = true, hasAvatar = true))
        assertEquals("/api/v1/identities/7/avatar", OwnPicture.path(m, null))
        assertEquals("/api/v1/identities/7/avatar", OwnPicture.path(m, "ME@example.com"))
    }

    @Test fun accountPictureIsTheFallback() {
        val m = me(Identity(7, "me@example.com", isDefault = true), Identity(8, "work@example.org", hasAvatar = true))
        assertEquals("/api/v1/me/picture?v=42", OwnPicture.path(m, null))
        assertEquals("/api/v1/identities/8/avatar", OwnPicture.path(m, "work@example.org"))
    }

    @Test fun initialsWithoutPictures() {
        assertNull(OwnPicture.path(me(Identity(7, "me@example.com", isDefault = true), picture = null), null))
        assertNull(OwnPicture.path(me(Identity(7, "me@example.com"), picture = AccountPicture(has = false)), null))
        assertNull(OwnPicture.path(null, "me@example.com"))
    }

    @Test fun notForOtherAddressesUnlessTheMessageIsMine() {
        val m = me(Identity(7, "me@example.com", isDefault = true))
        assertNull(OwnPicture.path(m, "stranger@example.org"))
        // A message you sent from an address mailr does not list (an alias): your account picture.
        assertEquals("/api/v1/me/picture?v=42", OwnPicture.path(m, "alias@example.com", mine = true))
    }

    @Test fun parsesTheServerFields() {
        val m = MailrJson.decodeFromString(Me.serializer(), """
            {"user":{"id":1,"email":"me@example.com"},"identities":[{"id":3,"email":"me@example.com","has_avatar":true}],
             "picture":{"has":true,"source":"idp","version":1700000000,"idp_available":true}}""".trimIndent())
        assertEquals(true, m.identities[0].hasAvatar)
        assertEquals("idp", m.picture?.source)
        assertEquals("/api/v1/identities/3/avatar", OwnPicture.path(m, null))
    }
}
