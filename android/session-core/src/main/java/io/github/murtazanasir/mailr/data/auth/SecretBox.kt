package io.github.murtazanasir.mailr.data.auth

import io.github.murtazanasir.mailr.util.catching

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * AES-256-GCM for small values kept in SharedPreferences, with a key that lives in the Android
 * Keystore and never leaves it. It replaces EncryptedSharedPreferences (androidx
 * security-crypto, deprecated upstream). The preference name is bound to each value as
 * associated data, so one stored value cannot be moved under another name.
 *
 * Sealed text is "v1:" + base64(iv + ciphertext).
 */
class SecretBox(private val key: () -> SecretKey) {
    fun seal(name: String, plain: String): String {
        val c = Cipher.getInstance(TRANSFORM)
        c.init(Cipher.ENCRYPT_MODE, key())
        c.updateAAD(name.toByteArray())
        val ct = c.doFinal(plain.toByteArray(Charsets.UTF_8))
        return PREFIX + Base64.encodeToString(c.iv + ct, Base64.NO_WRAP)
    }

    /** The plain value, or null when [sealed] is not ours or does not verify. */
    fun open(name: String, sealed: String): String? = catching {
        if (!sealed.startsWith(PREFIX)) return null
        val raw = Base64.decode(sealed.removePrefix(PREFIX), Base64.NO_WRAP)
        val c = Cipher.getInstance(TRANSFORM)
        c.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, raw, 0, IV_BYTES))
        c.updateAAD(name.toByteArray())
        String(c.doFinal(raw, IV_BYTES, raw.size - IV_BYTES), Charsets.UTF_8)
    }.getOrNull()

    companion object {
        const val PREFIX = "v1:"
        private const val TRANSFORM = "AES/GCM/NoPadding"
        private const val IV_BYTES = 12

        fun isSealed(v: String?) = v?.startsWith(PREFIX) == true

        /** A box whose key is the Keystore key [alias] (made on first use). */
        fun keystore(alias: String): SecretBox {
            var cached: SecretKey? = null
            return SecretBox {
                cached ?: synchronized(this) {
                    val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
                    ((ks.getKey(alias, null) as? SecretKey) ?: KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
                        init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                            .setKeySize(256).build())
                        generateKey()
                    }).also { cached = it }
                }
            }
        }

        /** Robolectric (unit and screenshot tests) has no Android Keystore. */
        val isRobolectric: Boolean get() = android.os.Build.FINGERPRINT == "robolectric"
    }
}
