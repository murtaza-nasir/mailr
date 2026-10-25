package io.github.murtazanasir.mailr.data.auth

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

/**
 * Sign-out that could not reach the server (offline): the old session is kept in
 * [SessionStore.pendingLogout] and ended on the server as soon as there is a network, so a
 * phone signed out offline does not leave a working session behind.
 */
object LogoutWork {
    fun enqueue(context: Context) {
        WorkManager.getInstance(context).enqueueUniqueWork("logout-retry", ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<LogoutRetryWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS).build())
    }

    /** No interceptors, no redirects: the old token goes to its own server and nowhere else. */
    private val http by lazy {
        OkHttpClient.Builder().followRedirects(false).followSslRedirects(false).callTimeout(20, TimeUnit.SECONDS).build()
    }

    /** True when the server no longer holds the session (ended now, or already gone). */
    suspend fun logout(p: PendingLogout): Boolean = withContext(Dispatchers.IO) {
        val req = Request.Builder().url(p.server.trimEnd('/') + "/api/v1/auth/native/logout")
            .header("Cookie", "mailr_session=${p.token}").header("X-CSRF-Token", p.csrf)
            .header("X-Mailr-Device", "android")
            .post("{}".toRequestBody(io.github.murtazanasir.mailr.data.api.ApiClient.JSON_TYPE)).build()
        http.newCall(req).execute().use { r -> r.isSuccessful || r.code == 401 || r.code == 403 }
    }
}

/** An Application that keeps a device session ([LogoutRetryWorker] finds the store here). */
interface SessionHost {
    val sessionStore: SessionStore
}

class LogoutRetryWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val sessions = (applicationContext as? SessionHost)?.sessionStore ?: return Result.success()
        val p = sessions.pendingLogout() ?: return Result.success()
        val done = catching { LogoutWork.logout(p) }.getOrDefault(false)
        if (done) sessions.clearPendingLogout()
        return when {
            done -> Result.success()
            runAttemptCount >= 20 -> { sessions.clearPendingLogout(); Result.failure() }
            else -> Result.retry()
        }
    }
}
