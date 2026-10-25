# kotlinx.serialization 1.7 ships the rules for @Serializable classes (companions, serializer(),
# $$serializer descriptors) in its jar (META-INF/com.android.tools/r8); nothing here repeats them.
# AppAuth needs no rules: its activities are kept through the manifest and it uses no reflection.
# OkHttp's optional TLS providers, absent on Android.
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
# Tink (security-crypto): compile-time annotations only.
-dontwarn com.google.errorprone.annotations.**
-dontwarn javax.annotation.**
