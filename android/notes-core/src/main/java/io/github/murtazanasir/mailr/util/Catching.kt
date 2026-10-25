package io.github.murtazanasir.mailr.util

import kotlin.coroutines.cancellation.CancellationException

/**
 * [runCatching] that lets cancellation through. Inside a coroutine, `runCatching` also catches the
 * CancellationException that ends it, so the code after it goes on in a coroutine that was cancelled
 * (and may write Compose state after its screen has gone). Use this instead;
 * `RunCatchingTest` fails on a new `runCatching` in the apps' main code.
 */
inline fun <T> catching(block: () -> T): Result<T> = try {
    Result.success(block())
} catch (e: CancellationException) {
    throw e
} catch (e: Throwable) {
    Result.failure(e)
}
