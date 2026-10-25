package io.github.murtazanasir.mailr.data.auth

import android.app.Activity
import android.app.ActivityManager
import android.net.Uri
import android.util.Log
import net.openid.appauth.AuthorizationManagementActivity

/**
 * The sign-in provider's answer (<scheme>:/oauth2redirect) for either app, in place of AppAuth's
 * own RedirectUriReceiverActivity.
 *
 * The apps' main activities have no task affinity, so the browser's
 * redirect opens in a task of its own, and AppAuth's receiver there would start a second, empty
 * AuthorizationManagementActivity ("No stored state", the sign-in reads as cancelled). [forward]
 * hands the answer to the AuthorizationManagementActivity waiting in the main activity's task.
 *
 * The redirect activity is exported, so any app can open it. Only the expected redirect with the
 * state of the sign-in in progress is forwarded ([PendingAuthorization.accepts]); anything
 * else, such as another app's crafted error answer, is dropped.
 */
object SignInRedirect {
    fun forward(activity: Activity, uri: Uri?, mainActivity: String) {
        if (!PendingAuthorization(activity).accepts(uri)) {
            Log.i("mailr.auth", "a sign-in answer that matches no sign-in in progress was dropped")
            return
        }
        val answer = AuthorizationManagementActivity.createResponseHandlingIntent(activity, uri)
        val signIn = activity.getSystemService(ActivityManager::class.java)?.appTasks?.firstOrNull { t ->
            t.taskInfo.baseActivity?.className == mainActivity
        }
        if (signIn != null) signIn.startActivity(activity, answer, null) else activity.startActivity(answer)
    }
}
