package io.github.murtazanasir.mailr.ui.search

import io.github.murtazanasir.mailr.util.catching

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.Search
import io.github.murtazanasir.mailr.look.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.Person
import io.github.murtazanasir.mailr.ui.components.Avatar
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R

/** Gmail's search chips map to operators (the chip's words, then the operator). */
val SEARCH_CHIPS = listOf(
    R.string.mail_search_chip_unread to "is:unread",
    R.string.mail_search_chip_starred to "is:starred",
    R.string.mail_search_chip_attachment to "has:attachment",
    R.string.mail_search_chip_week to "newer_than:7d",
    R.string.mail_search_chip_important to "is:important",
    R.string.mail_search_chip_sent_by_me to "from:me",
)

@Composable
fun SearchScreen(
    initial: String,
    recent: List<String>,
    people: List<Person>,
    onQueryChange: (String) -> Unit,
    onSearch: (String) -> Unit,
    onBack: () -> Unit,
) {
    var value by remember { mutableStateOf(TextFieldValue(initial, TextRange(initial.length))) }
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { catching { focus.requestFocus() } }

    fun toggleChip(op: String) {
        val t = value.text
        val next = if (op in t.split(' ')) t.split(' ').filter { it != op }.joinToString(" ") else "$t $op".trim()
        value = TextFieldValue(next, TextRange(next.length))
        onQueryChange(next)
    }

    Column(Modifier.fillMaxSize().statusBarsPadding().imePadding()) {
        Surface(color = MaterialTheme.colorScheme.surfaceContainerHigh, shape = MaterialTheme.shapes.extraLarge,
            modifier = Modifier.fillMaxWidth().padding(12.dp)) {
            Row {
                IconButton(onClick = onBack, modifier = Modifier.padding(start = 4.dp, top = 4.dp)) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.mail_back))
                }
                TextField(
                    value = value,
                    onValueChange = { value = it; onQueryChange(it.text) },
                    placeholder = { Text(stringResource(R.string.mail_search_placeholder)) },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search, autoCorrectEnabled = false),
                    keyboardActions = KeyboardActions(onSearch = { if (value.text.isNotBlank()) onSearch(value.text) }),
                    trailingIcon = {
                        if (value.text.isNotEmpty()) IconButton(onClick = { value = TextFieldValue(""); onQueryChange("") }) {
                            Icon(Icons.Outlined.Close, stringResource(R.string.mail_search_clear))
                        }
                    },
                    colors = TextFieldDefaults.colors(
                        focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent,
                        focusedIndicatorColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent,
                    ),
                    modifier = Modifier.fillMaxWidth().focusRequester(focus),
                )
            }
        }
        Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SEARCH_CHIPS.forEach { (label, op) ->
                FilterChip(selected = op in value.text.split(' '), onClick = { toggleChip(op) }, label = { Text(stringResource(label)) })
            }
        }
        LazyColumn(Modifier.fillMaxSize()) {
            if (people.isNotEmpty()) {
                items(people, key = { "p-" + it.email }) { p ->
                    ListItem(
                        headlineContent = { Text(p.name.ifBlank { p.email }) },
                        supportingContent = { Text(p.email) },
                        leadingContent = { Avatar(p.name, p.email, size = 36.dp) },
                        colors = ListItemDefaults.colors(containerColor = Color.Transparent),
                        modifier = Modifier.clickable { onSearch("from:${p.email}") },
                    )
                }
                item { HorizontalDivider() }
            }
            if (value.text.isNotBlank()) {
                item {
                    ListItem(
                        headlineContent = { Text(stringResource(R.string.mail_search_for, value.text)) },
                        leadingContent = { Icon(Icons.Outlined.Search, null) },
                        colors = ListItemDefaults.colors(containerColor = Color.Transparent),
                        modifier = Modifier.clickable { onSearch(value.text) },
                    )
                }
            }
            items(recent.filter { value.text.isBlank() || it.contains(value.text, ignoreCase = true) }, key = { "r-$it" }) { r ->
                ListItem(
                    headlineContent = { Text(r) },
                    leadingContent = { Icon(Icons.Outlined.History, null) },
                    colors = ListItemDefaults.colors(containerColor = Color.Transparent),
                    modifier = Modifier.clickable { onSearch(r) },
                )
            }
            if (value.text.isBlank() && recent.isEmpty()) {
                item {
                    Text(
                        stringResource(R.string.mail_search_tip),
                        style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(20.dp),
                    )
                }
            }
        }
    }
}
