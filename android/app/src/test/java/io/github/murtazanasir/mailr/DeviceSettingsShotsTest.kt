package io.github.murtazanasir.mailr

import android.app.Application
import io.github.murtazanasir.mailr.data.api.NativeConfig
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingCallbacks
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingScreen
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingState
import io.github.murtazanasir.mailr.ui.settings.SettingsCallbacks
import io.github.murtazanasir.mailr.ui.settings.SettingsScreen
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** Settings and the first-run screens. On every device of [DeviceShots], light and dark. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class DeviceSettingsShotsTest(device: String, qualifiers: String, dark: Boolean) : DeviceShots(device, qualifiers, dark) {
    @Test fun settings() = shot("settings") {
        SettingsScreen(Prefs(), "Alex Morgan", "me@example.com", "https://mail.example.com", emptyList(),
            SettingsCallbacks({}, {}, {}, {}, {}))
    }

    @Test fun onboardingServer() = shot("onboarding-server") {
        OnboardingScreen(OnboardingState("https://mail.example.com"), OnboardingCallbacks({}, {}, {}, {}, { _, _ -> }), io.github.murtazanasir.mailr.ui.onboarding.MailrBrand)
    }

    @Test fun onboardingSignIn() = shot("onboarding-signin") {
        OnboardingScreen(OnboardingState("https://mail.example.com",
            NativeConfig(true, "https://id.example.com", "mailr-android", "io.github.murtazanasir.mailr:/oauth2redirect")),
            OnboardingCallbacks({}, {}, {}, {}, { _, _ -> }), io.github.murtazanasir.mailr.ui.onboarding.MailrBrand)
    }
}
