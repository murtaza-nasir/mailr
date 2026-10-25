package io.github.murtazanasir.mailr.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import io.github.murtazanasir.mailr.i18n.AssistantName

/** The assistant's name in a composable, current as it changes (AssistantName; Settings → Ambrose → About you on the web). */
@Composable
fun assistantName(): String {
    val name by AssistantName.name.collectAsState()
    return name
}
