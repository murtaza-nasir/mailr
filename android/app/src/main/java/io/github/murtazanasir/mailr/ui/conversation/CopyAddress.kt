package io.github.murtazanasir.mailr.ui.conversation

import io.github.murtazanasir.mailr.util.catching

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Build
import android.os.PersistableBundle
import android.widget.Toast
import androidx.annotation.StringRes
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.semantics
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem

/**
 * ACT mail.copy_address @android.reader: a long-press on a sender's or recipient's name or address
 * offers Copy address, Copy name and Copy name and address (the web reader's menu, AddressCopy.tsx).
 * TalkBack lists the same choices as custom actions.
 */
object AddressCopy {
    enum class Kind(@StringRes val label: Int) {
        ADDRESS(R.string.conv_copy_address),
        NAME(R.string.conv_copy_name),
        NAME_AND_ADDRESS(R.string.conv_copy_name_and_address),
    }

    /** The choices for [a]: the address alone when the message gives no name. */
    fun choices(a: Address): List<Kind> = if (a.name.isNullOrBlank()) listOf(Kind.ADDRESS) else Kind.entries

    /** What [kind] puts on the clipboard. */
    fun text(a: Address, kind: Kind): String = when (kind) {
        Kind.ADDRESS -> a.email
        Kind.NAME -> a.name?.trim().orEmpty().ifBlank { a.email }
        Kind.NAME_AND_ADDRESS -> nameAndAddress(a)
    }

    /** "Name <address>", ready to paste into a To field; a name with a comma, quote or bracket is
     *  quoted ("Nguyen, Bich" <bich@example.com>). The same rule as the web (lib/clipboard.ts). */
    fun nameAndAddress(a: Address): String {
        val name = a.name?.trim().orEmpty()
        if (name.isEmpty()) return a.email
        val quoted = if (Regex("""[,;:<>()\[\]"@\\]""").containsMatchIn(name)) "\"" + name.replace(Regex("""(["\\])"""), """\\$1""") + "\"" else name
        return "$quoted <${a.email}>"
    }

    /**
     * Put [text] on the clipboard, marked not sensitive (an address may show in the keyboard's
     * clipboard history and in the system's "copied" preview). Android 13+ confirms the copy
     * itself; older versions get a short toast.
     */
    fun copy(ctx: Context, text: String) {
        val cm = ctx.getSystemService(ClipboardManager::class.java) ?: return
        val clip = ClipData.newPlainText("mailr", text)
        // ClipDescription.EXTRA_IS_SENSITIVE on API 33+; keyboards honour the same key before that.
        clip.description.extras = PersistableBundle().apply { putBoolean("android.content.extra.IS_SENSITIVE", false) }
        catching { cm.setPrimaryClip(clip) }
        if (Build.VERSION.SDK_INT < 33) Toast.makeText(ctx, ctx.getString(R.string.ui_shell_copied), Toast.LENGTH_SHORT).show()
    }
}

/**
 * A name or an address in the reader's header that can be copied: a long-press opens the copy
 * menu, a tap does [onClick] (the header's own tap), TalkBack offers the choices as actions.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun CopyableAddress(a: Address, onClick: () -> Unit, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val ctx = LocalContext.current
    var menu by remember { mutableStateOf(false) }
    val choices = AddressCopy.choices(a).map { it to stringResource(it.label) }
    Box(
        modifier
            .combinedClickable(
                interactionSource = null, indication = null,
                onLongClickLabel = stringResource(R.string.conv_copy_choices),
                onLongClick = { menu = true },
                onClick = onClick,
            )
            .semantics {
                customActions = choices.map { (kind, label) ->
                    CustomAccessibilityAction(label) { AddressCopy.copy(ctx, AddressCopy.text(a, kind)); true }
                }
            },
    ) {
        content()
        DropdownMenu(menu, { menu = false }) {
            choices.forEach { (kind, label) ->
                DropdownMenuItem({ Text(label) }, { menu = false; AddressCopy.copy(ctx, AddressCopy.text(a, kind)) })
            }
        }
    }
}
