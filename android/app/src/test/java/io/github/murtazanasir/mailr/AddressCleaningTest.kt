package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.data.api.Attendee
import io.github.murtazanasir.mailr.data.api.Invite
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.Participant
import io.github.murtazanasir.mailr.data.api.PrepGuest
import io.github.murtazanasir.mailr.data.api.SafeText
import org.junit.Assert.assertEquals
import org.junit.Test

/** Invitation organizer and guest addresses (and every other address the
 *  app decodes) lose bidi, invisible and control characters, as on the server and the web. */
class AddressCleaningTest {
    private val rlo = "‮"

    @Test fun inviteOrganizerAndAttendeesAreCleaned() {
        val inv = MailrJson.decodeFromString(Invite.serializer(),
            """{"organizer":{"email":"ceo@exa${rlo}mple.com​","name":"C${rlo}EO"},
               "attendees":[{"email":" bob@exa⁦mple.org\u0007","name":"Bob","status":"ACCEPTED"}]}""")
        assertEquals("ceo@example.com", inv.organizer.email)
        assertEquals("CEO", inv.organizer.name)
        assertEquals("bob@example.org", inv.attendees.single().email)
    }

    @Test fun otherAddressFieldsAreCleaned() {
        assertEquals("a@example.com", MailrJson.decodeFromString(Address.serializer(), """{"email":"a@ex${rlo}ample.com"}""").email)
        assertEquals("p@example.com", MailrJson.decodeFromString(Participant.serializer(), """{"email":"p@exa‍mple.com"}""").email)
        val att = MailrJson.decodeFromString(Attendee.serializer(), """{"email":"g@ex‏ample.com","name":"G${rlo}uest"}""")
        assertEquals("g@example.com", att.email)
        assertEquals("Guest", att.name)
        assertEquals("h@example.com", MailrJson.decodeFromString(PrepGuest.serializer(), """{"email":"h@exa﻿mple.com"}""").email)
    }

    @Test fun cleanAddressKeepsPlainAddresses() {
        for (a in listOf("jane+tag@example.com", "jürgen@bücher.example", "o'brien@example.ie", "x@xn--bcher-kva.example")) {
            assertEquals(a, SafeText.cleanAddress(a))
        }
    }
}
