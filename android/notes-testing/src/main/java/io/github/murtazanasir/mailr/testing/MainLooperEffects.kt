package io.github.murtazanasir.mailr.testing

import android.os.Handler
import android.os.Looper
import androidx.compose.ui.test.junit4.ComposeContentTestRule
import kotlinx.coroutines.CancellableContinuation
import kotlinx.coroutines.Delay
import kotlinx.coroutines.DisposableHandle
import kotlinx.coroutines.InternalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.Runnable
import org.junit.rules.TestRule
import kotlin.coroutines.AbstractCoroutineContextElement
import kotlin.coroutines.Continuation
import kotlin.coroutines.ContinuationInterceptor
import kotlin.coroutines.CoroutineContext

/**
 * The Compose rule for this project's tests: `createComposeRule()` whose screens' coroutines
 * (LaunchedEffect, rememberCoroutineScope, collectAsState) carry on in the main thread after they
 * waited on another thread, as they do on a phone.
 *
 * Compose 1.7's test environment gives a composition's effects an UnconfinedTestDispatcher. A
 * coroutine that resumes after a network call or a Room query then carries on in the thread that
 * resumed it: it writes Compose state on an OkHttp or Room thread while the main thread composes and
 * reads the tree, and now and then the screen never shows that write (a sheet that stays on
 * "Loading…" although the fake server answered seconds earlier). On a phone Compose's dispatcher
 * posts the resumption to the main thread.
 *
 * Here a resumption that comes from another thread is posted to the main looper, which the rule's
 * waitForIdle runs. A resumption on the main thread is left as the test environment runs it (inline,
 * with its frame and delay handling), so the test clock, gestures and animations work as before.
 */
fun createComposeRule(): ComposeContentTestRule =
    androidx.compose.ui.test.junit4.createComposeRule().effectsOnMainLooper()

/** Makes [this] Compose rule's effects carry on in the main thread (see [createComposeRule]). */
fun <T : TestRule> T.effectsOnMainLooper(): T {
    try {
        val env = javaClass.getDeclaredField("environment").apply { isAccessible = true }.get(this)
        val recomposer = generateSequence<Class<*>>(env.javaClass) { it.superclass }.firstNotNullOf { c ->
            c.declaredFields.firstOrNull { it.name == "recomposer" }
        }.apply { isAccessible = true }.get(env)
        val field = recomposer.javaClass.getDeclaredField("effectCoroutineContext").apply { isAccessible = true }
        val old = field.get(recomposer) as CoroutineContext
        val inner = old[ContinuationInterceptor]
        if (inner != null && inner !is MainThreadResume) field.set(recomposer, old + MainThreadResume(inner))
    } catch (e: ReflectiveOperationException) {
        throw AssertionError("Compose's test environment changed: its effects can no longer be kept on the main thread. " +
            "From Compose 1.8, ComposeUiTestFlags.isStandardTestDispatcherSupportEnabled may do this instead.", e)
    }
    return this
}

/**
 * Wraps the test environment's interceptor: each continuation it hands out first passes through
 * [MainHop], which moves a resumption from another thread to the main looper. Delays go to the
 * wrapped interceptor (the test clock).
 */
@OptIn(InternalCoroutinesApi::class)
private class MainThreadResume(private val inner: ContinuationInterceptor) :
    AbstractCoroutineContextElement(ContinuationInterceptor), ContinuationInterceptor, Delay {

    override fun <T> interceptContinuation(continuation: Continuation<T>): Continuation<T> =
        inner.interceptContinuation(MainHop(continuation))

    override fun releaseInterceptedContinuation(continuation: Continuation<*>) =
        inner.releaseInterceptedContinuation(continuation)

    private val delay get() = inner as? Delay ?: error("the test environment's interceptor has no delay")

    override fun scheduleResumeAfterDelay(timeMillis: Long, continuation: CancellableContinuation<Unit>) =
        delay.scheduleResumeAfterDelay(timeMillis, continuation)

    override fun invokeOnTimeout(timeMillis: Long, block: Runnable, context: CoroutineContext): DisposableHandle =
        delay.invokeOnTimeout(timeMillis, block, context)

    override fun toString() = "MainThreadResume($inner)"
}

@OptIn(InternalCoroutinesApi::class)
private class MainHop<T>(private val next: Continuation<T>) : Continuation<T> {
    override val context: CoroutineContext get() = next.context

    override fun resumeWith(result: Result<T>) {
        if (Looper.myLooper() == Looper.getMainLooper()) return next.resumeWith(result)
        Handler(Looper.getMainLooper()).post {
            // As a dispatcher does: a coroutine cancelled meanwhile resumes with its cancellation.
            val job = next.context[Job]
            if (job != null && !job.isActive) next.resumeWith(Result.failure(job.getCancellationException()))
            else next.resumeWith(result)
        }
    }
}
