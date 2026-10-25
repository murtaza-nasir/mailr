package io.github.murtazanasir.mailr.ui.onboarding

import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/** The mailr app on the shared sign-in screen (android/session-core). */
val MailrBrand: OnboardingBrand
    get() = OnboardingBrand("Mailr", AppText.get(R.string.onb_tagline), R.drawable.ic_launcher_monochrome)
