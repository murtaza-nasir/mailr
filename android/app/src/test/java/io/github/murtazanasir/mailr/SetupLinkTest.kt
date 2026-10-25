package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.ui.onboarding.parseSetupServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** mailr://setup?server=… from the web setup guide's Get the app page (ui/onboarding/Onboarding.kt). */
class SetupLinkTest {
    @Test fun readsTheServer() {
        assertEquals("https://mail.example.com", parseSetupServer("mailr://setup?server=https%3A%2F%2Fmail.example.com"))
        assertEquals("https://mail.example.com", parseSetupServer("mailr://setup?server=https%3A%2F%2FMail.Example.com%2F"))
        assertEquals("http://192.168.1.20:8025", parseSetupServer("mailr://setup?server=http%3A%2F%2F192.168.1.20%3A8025"))
        assertEquals("https://mail.example.com", parseSetupServer("MAILR://SETUP?x=1&server=https://mail.example.com"))
    }

    @Test fun refusesAnythingElse() {
        for (bad in listOf(null, "", "mailr://inbox?server=https%3A%2F%2Fa.org", "https://setup?server=https://a.org",
            "mailr://setup", "mailr://setup?server=", "mailr://setup?server=ftp%3A%2F%2Fa.org",
            "mailr://setup?server=javascript%3Aalert(1)", "mailr://setup?server=https%3A%2F%2Fuser%3Apw%40a.org",
            "mailr://setup?server=https%3A%2F%2Fa.org%2Fphish", "mailr://setup?server=https%3A%2F%2Fa.org%3Fq%3D1",
            "mailr://setup?server=" + "a".repeat(700))) {
            assertNull(bad, parseSetupServer(bad))
        }
    }
}
