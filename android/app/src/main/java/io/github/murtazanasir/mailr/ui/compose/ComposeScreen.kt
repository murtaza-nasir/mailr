package io.github.murtazanasir.mailr.ui.compose

import io.github.murtazanasir.mailr.util.catching

import kotlinx.coroutines.launch
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.components.refuseObscuredTouches
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.outlined.AddPhotoAlternate
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material.icons.outlined.PhotoCamera
import androidx.compose.material.icons.outlined.PhotoLibrary
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.runtime.key
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.fromHtml
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.outlined.ArrowDropDown
import androidx.compose.material.icons.outlined.AttachFile
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.automirrored.outlined.InsertDriveFile
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.automirrored.outlined.ScheduleSend
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import io.github.murtazanasir.mailr.look.InputChip
import io.github.murtazanasir.mailr.look.LinearProgressIndicator
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import io.github.murtazanasir.mailr.look.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.ui.components.Avatar
import io.github.murtazanasir.mailr.ui.util.Format
import java.time.DayOfWeek
import java.time.ZonedDateTime
import java.time.temporal.TemporalAdjusters
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.LocaleFormats

data class ComposeCallbacks(
    val close: () -> Unit,
    val discard: () -> Unit,
    val send: (ZonedDateTime?) -> Unit,
    val pickSchedule: () -> Unit,
    val attach: () -> Unit,
    val setIdentity: (Long) -> Unit,
    val setSubject: (String) -> Unit,
    val setBody: (String) -> Unit,
    val addRecipient: (Field, String) -> Boolean,
    val addPerson: (Field, io.github.murtazanasir.mailr.data.api.Person) -> Unit,
    val removeRecipient: (Field, Address) -> Unit,
    val query: (Field, String) -> Unit,
    val toggleCc: () -> Unit,
    val toggleQuoted: () -> Unit,
    val removeAttachment: (io.github.murtazanasir.mailr.data.api.ComposeAttachment) -> Unit,
    /** The snackbar's Undo after a removed attachment puts it back. */
    val restoreAttachment: (io.github.murtazanasir.mailr.data.api.ComposeAttachment) -> Unit = {},
    // The body's blocks (ComposeBody) and inline photos (ACT mail.insert_image @android.editor).
    val setText: (Long, String) -> Unit = { _, _ -> },
    val setCursor: (Long, Int) -> Unit = { _, _ -> },
    val removeBlock: (Long) -> Unit = {},
    val editAsText: (Long) -> Unit = {},
    val takePhoto: () -> Unit = {},
    val pickPhotos: () -> Unit = {},
    /** ACT template.insert @android.editor: the templates (null: the menu has no entry) and the pick. */
    val templates: (suspend () -> List<io.github.murtazanasir.mailr.data.api.MailTemplate>)? = null,
    val insertTemplate: (io.github.murtazanasir.mailr.data.api.MailTemplate) -> Unit = {},
)

@Composable
fun ComposeScreen(s: ComposeState, snackbar: SnackbarHostState, cb: ComposeCallbacks,
                  /** The live-editing bars (LiveBars), above the fields. */
                  live: (@Composable () -> Unit)? = null) {
    // Other apps' overlays are hidden while the composer and its Send button show (no screenshot change).
    io.github.murtazanasir.mailr.ui.components.HideOverlays()
    var menu by remember { mutableStateOf(false) }
    var scheduleMenu by remember { mutableStateOf(false) }
    var photoMenu by remember { mutableStateOf(false) }
    var templatesOpen by remember { mutableStateOf(false) }
    val bodyFocus = remember { FocusRequester() }
    Scaffold(
        contentWindowInsets = WindowInsets(0),
        topBar = {
            TopAppBar(
                title = { Text(s.title, maxLines = 1) },
                navigationIcon = { IconButton(onClick = cb.close) { Icon(Icons.Outlined.Close, stringResource(R.string.compose_close_save)) } },
                actions = {
                    if (s.saveStatus.isNotEmpty()) {
                        Text(s.saveStatus, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(end = 4.dp))
                    }
                    // ACT mail.insert_image: a photo into the body at the cursor. Attach stays separate.
                    Box {
                        IconButton(onClick = { photoMenu = true }, enabled = !s.loading) { Icon(Icons.Outlined.AddPhotoAlternate, stringResource(R.string.compose_insert_photo)) }
                        DropdownMenu(photoMenu, { photoMenu = false }) {
                            DropdownMenuItem({ Text(stringResource(R.string.compose_take_photo)) }, { photoMenu = false; cb.takePhoto() },
                                leadingIcon = { Icon(Icons.Outlined.PhotoCamera, null) })
                            DropdownMenuItem({ Text(stringResource(R.string.compose_choose_photos)) }, { photoMenu = false; cb.pickPhotos() },
                                leadingIcon = { Icon(Icons.Outlined.PhotoLibrary, null) })
                        }
                    }
                    IconButton(onClick = cb.attach, enabled = !s.loading) { Icon(Icons.Outlined.AttachFile, stringResource(R.string.compose_attach_files)) }
                    Box {
                        // ACT draft.send @android.editor; the menu holds ACT draft.schedule @android.editor and ACT draft.discard @android.editor; Close does ACT draft.save @android.editor.
                        IconButton(onClick = { cb.send(null) }, enabled = !s.sending && !s.loading, modifier = Modifier.refuseObscuredTouches()) {
                            Icon(Icons.AutoMirrored.Filled.Send, stringResource(R.string.compose_send), tint = MaterialTheme.colorScheme.primary)
                        }
                    }
                    IconButton(onClick = { menu = true }, enabled = !s.loading) { Icon(Icons.Outlined.MoreVert, stringResource(R.string.compose_more)) }
                    DropdownMenu(menu, { menu = false }) {
                        if (cb.templates != null) DropdownMenuItem({ Text(stringResource(R.string.compose_insert_template)) }, { menu = false; templatesOpen = true },
                            leadingIcon = { Icon(Icons.Outlined.Description, null) })
                        DropdownMenuItem({ Text(stringResource(R.string.compose_schedule_send)) }, { menu = false; scheduleMenu = true },
                            leadingIcon = { Icon(Icons.AutoMirrored.Outlined.ScheduleSend, null) })
                        DropdownMenuItem({ Text(stringResource(R.string.compose_discard)) }, { menu = false; cb.discard() },
                            leadingIcon = { Icon(Icons.Outlined.Delete, null) })
                    }
                    DropdownMenu(scheduleMenu, { scheduleMenu = false }) {
                        scheduleOptions().forEach { (label, at) ->
                            DropdownMenuItem(
                                text = { Column { Text(stringResource(label)); Text(at.format(LocaleFormats.date("EEEMMMdhmma", "EEE, MMM d, h:mm a")),
                                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) } },
                                onClick = { scheduleMenu = false; cb.send(at) },
                                modifier = Modifier.refuseObscuredTouches(),
                            )
                        }
                        DropdownMenuItem({ Text(stringResource(R.string.compose_pick_date_time)) }, { scheduleMenu = false; cb.pickSchedule() })
                    }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize().imePadding(), contentAlignment = Alignment.TopCenter) {
            Column(
                Modifier.widthIn(max = 760.dp).fillMaxWidth().verticalScroll(rememberScrollState()).navigationBarsPadding(),
            ) {
                if (s.sending || s.loading) LinearProgressIndicator(Modifier.fillMaxWidth())
                live?.invoke()
                // Until the stored copy arrives there is nothing to type in: what would be typed
                // there now could only be replaced by the copy.
                if (s.loading) {
                    Text(s.loadingText, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 20.dp, vertical = 16.dp))
                } else Fields(s, cb, bodyFocus, snackbar)
            }
        }
    }
    LaunchedEffect(s.loading) {
        if (!s.loading && s.to.isNotEmpty() && s.body.isBlank()) catching { bodyFocus.requestFocus() }
    }
    if (templatesOpen && cb.templates != null) TemplatesSheet(cb.templates, onDismiss = { templatesOpen = false }) { t ->
        templatesOpen = false
        cb.insertTemplate(t)
    }
}

/** From, the recipients, the subject, the body, the quote and the attachments. */
@Composable
private fun Fields(s: ComposeState, cb: ComposeCallbacks, bodyFocus: FocusRequester, snackbar: SnackbarHostState) {
    Column {
        FromRow(s, cb)
        Divider()
        RecipientField(stringResource(R.string.compose_to), Field.TO, s.to, s, cb, trailing = {
            IconButton(onClick = cb.toggleCc) {
                Icon(if (s.showCc) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore,
                    if (s.showCc) stringResource(R.string.compose_hide_cc) else stringResource(R.string.compose_show_cc))
            }
        })
        if (s.showCc) {
            Divider(); RecipientField(stringResource(R.string.compose_cc), Field.CC, s.cc, s, cb)
            Divider(); RecipientField(stringResource(R.string.compose_bcc), Field.BCC, s.bcc, s, cb)
        }
        Divider()
        PlainField(s.subject, cb.setSubject, stringResource(R.string.compose_subject), singleLine = true, capitalization = KeyboardCapitalization.Sentences,
            imeAction = ImeAction.Next, onNext = { bodyFocus.requestFocus() })
        Divider()
        Body(s, cb, bodyFocus)
        if (s.quoted != null) {
            TextButton(onClick = cb.toggleQuoted, modifier = Modifier.padding(start = 8.dp)) {
                Text(if (s.showQuoted) stringResource(R.string.compose_hide_quoted) else stringResource(R.string.compose_show_quoted))
            }
            if (s.showQuoted) {
                Text(Format.htmlToText(s.quoted), style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp))
            }
        }
        if (s.attachments.isNotEmpty() || s.uploading > 0) Attachments(s, cb, snackbar)
        Spacer(Modifier.size(24.dp))
    }
}

private fun scheduleOptions(now: ZonedDateTime = AppClock.now()): List<Pair<Int, ZonedDateTime>> {
    val tomorrowMorning = now.plusDays(1).withHour(8).withMinute(0).withSecond(0).withNano(0)
    val tomorrowAfternoon = now.plusDays(1).withHour(13).withMinute(0).withSecond(0).withNano(0)
    val monday = now.with(TemporalAdjusters.next(DayOfWeek.MONDAY)).withHour(8).withMinute(0).withSecond(0).withNano(0)
    return listOf(R.string.compose_tomorrow_morning to tomorrowMorning, R.string.compose_tomorrow_afternoon to tomorrowAfternoon,
        R.string.compose_monday_morning to monday)
}

@Composable
private fun Divider() = HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.6f))

@Composable
private fun FromRow(s: ComposeState, cb: ComposeCallbacks) {
    var open by remember { mutableStateOf(false) }
    val id = s.identities.firstOrNull { it.id == s.identityId }
    Row(Modifier.fillMaxWidth().clickable(enabled = s.identities.size > 1) { open = true }.padding(horizontal = 20.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Text(stringResource(R.string.compose_from), style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(56.dp))
        Text(id?.let { if (it.name.isNotBlank()) stringResource(R.string.compose_identity, it.name, it.email) else it.email }
            ?: stringResource(R.string.compose_no_sending_address),
            style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        if (s.identities.size > 1) Icon(Icons.Outlined.ArrowDropDown, stringResource(R.string.compose_choose_address))
        DropdownMenu(open, { open = false }) {
            s.identities.forEach { i ->
                DropdownMenuItem({ Column { Text(i.name.ifBlank { i.email }); Text(i.email, style = MaterialTheme.typography.bodySmall) } },
                    { open = false; cb.setIdentity(i.id) })
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun RecipientField(
    label: String,
    field: Field,
    values: List<Address>,
    s: ComposeState,
    cb: ComposeCallbacks,
    trailing: @Composable () -> Unit = {},
) {
    var text by remember { mutableStateOf("") }
    var focused by remember { mutableStateOf(false) }
    val fieldDesc = stringResource(R.string.compose_field_recipients, label)
    Column {
        Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(label, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.width(56.dp))
            FlowRow(Modifier.weight(1f).padding(vertical = 6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.Center) {
                values.forEach { a ->
                    InputChip(
                        selected = false,
                        onClick = { cb.removeRecipient(field, a) },
                        label = { Text(a.display, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                        avatar = { Avatar(a.name, a.email, size = 24.dp) },
                        trailingIcon = { Icon(Icons.Outlined.Close, stringResource(R.string.compose_remove_named, a.email), Modifier.size(16.dp)) },
                        modifier = Modifier.widthIn(max = 260.dp),
                    )
                }
                BasicTextField(
                    value = text,
                    onValueChange = { v ->
                        if (v.endsWith(",") || v.endsWith(";") || v.endsWith(" ") && '@' in v) {
                            if (cb.addRecipient(field, v)) text = "" else text = v
                        } else {
                            text = v
                            cb.query(field, v)
                        }
                    },
                    singleLine = true,
                    textStyle = MaterialTheme.typography.bodyLarge.copy(color = MaterialTheme.colorScheme.onSurface),
                    cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email, imeAction = ImeAction.Next, autoCorrectEnabled = false),
                    keyboardActions = KeyboardActions(onNext = { if (text.isNotBlank() && cb.addRecipient(field, text)) text = "" }),
                    modifier = Modifier.widthIn(min = 80.dp).padding(vertical = 10.dp)
                        .onFocusChanged {
                            if (focused && !it.isFocused && text.isNotBlank() && cb.addRecipient(field, text)) text = ""
                            focused = it.isFocused
                        }
                        .onPreviewKeyEvent { e ->
                            if (e.key == Key.Backspace && text.isEmpty() && values.isNotEmpty()) {
                                cb.removeRecipient(field, values.last()); true
                            } else false
                        }
                        .semantics { contentDescription = fieldDesc },
                )
            }
            trailing()
        }
        if (s.suggestFor == field && s.suggestions.isNotEmpty() && focused) {
            Surface(tonalElevation = 3.dp, shadowElevation = 2.dp, modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp)) {
                Column {
                    s.suggestions.take(5).forEach { p ->
                        ListItem(
                            headlineContent = { Text(p.name.ifBlank { p.email }, maxLines = 1) },
                            supportingContent = { Text(p.email, maxLines = 1) },
                            leadingContent = { Avatar(p.name, p.email, size = 32.dp) },
                            colors = ListItemDefaults.colors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh),
                            modifier = Modifier.clickable { cb.addPerson(field, p); text = "" },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun PlainField(
    value: String,
    onChange: (String) -> Unit,
    placeholder: String,
    singleLine: Boolean,
    capitalization: KeyboardCapitalization = KeyboardCapitalization.None,
    imeAction: ImeAction = ImeAction.Default,
    onNext: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    Box(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 14.dp)) {
        if (value.isEmpty()) Text(placeholder, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        BasicTextField(
            value = value, onValueChange = onChange, singleLine = singleLine,
            textStyle = MaterialTheme.typography.bodyLarge.copy(color = MaterialTheme.colorScheme.onSurface),
            cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
            keyboardOptions = KeyboardOptions(capitalization = capitalization, imeAction = imeAction),
            keyboardActions = KeyboardActions(onNext = { onNext() }),
            modifier = modifier.fillMaxWidth().semantics { contentDescription = placeholder },
        )
    }
}

@Composable
private fun Attachments(s: ComposeState, cb: ComposeCallbacks, snackbar: SnackbarHostState) {
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    val removed = stringResource(R.string.compose_attachment_removed)
    val undo = stringResource(R.string.compose_undo)
    Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
        s.attachments.forEach { a ->
            Surface(shape = MaterialTheme.shapes.small, color = MaterialTheme.colorScheme.surfaceContainerHigh,
                modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                Row(Modifier.padding(start = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.AutoMirrored.Outlined.InsertDriveFile, null)
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f).padding(vertical = 10.dp)) {
                        Text(a.filename, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(Format.bytes(a.size), style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    IconButton(onClick = {
                        cb.removeAttachment(a)
                        // A removed attachment comes back with the snackbar's Undo.
                        scope.launch {
                            if (snackbar.showSnackbar(removed.format(a.filename), undo, duration = androidx.compose.material3.SnackbarDuration.Short) ==
                                androidx.compose.material3.SnackbarResult.ActionPerformed) cb.restoreAttachment(a)
                        }
                    }) { Icon(Icons.Outlined.Close, stringResource(R.string.compose_remove_named, a.filename)) }
                }
            }
        }
        if (s.uploading > 0) {
            Row(Modifier.padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                LinearProgressIndicator(Modifier.weight(1f))
                Spacer(Modifier.width(12.dp))
                Text(stringResource(R.string.compose_uploading, s.uploading), style = MaterialTheme.typography.labelMedium)
            }
        }
    }
}

/** The body: text blocks to type in, inline images with a remove button, and HTML kept from
 *  the draft (read-only, "Edit as plain text"). */
@Composable
private fun Body(s: ComposeState, cb: ComposeCallbacks, focus: FocusRequester) {
    val firstText = s.blocks.firstOrNull { it is BodyBlock.Text }?.key
    val lastText = s.blocks.lastOrNull { it is BodyBlock.Text }?.key
    val empty = !ComposeBody.hasContent(s.blocks)
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        s.blocks.forEach { b ->
            key(b.key) {
                when (b) {
                    is BodyBlock.Text -> BodyText(b, if (empty && b.key == firstText) stringResource(R.string.compose_body_placeholder) else null, cb,
                        Modifier.then(if (b.key == firstText) Modifier.focusRequester(focus) else Modifier)
                            .then(if (b.key == lastText) Modifier.heightIn(min = if (s.blocks.size == 1) 208.dp else 72.dp) else Modifier))
                    is BodyBlock.Image -> InlineImage(b, s.previews[b.key]) { cb.removeBlock(b.key) }
                    is BodyBlock.Html -> KeptHtml(b) { cb.editAsText(b.key) }
                }
            }
        }
    }
}

@Composable
private fun BodyText(b: BodyBlock.Text, placeholder: String?, cb: ComposeCallbacks, modifier: Modifier) {
    val messageText = stringResource(R.string.compose_message_text)
    // The field keeps its own selection; the text itself comes from the state.
    var local by remember(b.key) { mutableStateOf(TextFieldValue(b.text, TextRange(b.text.length))) }
    val value = if (local.text == b.text) local
        else TextFieldValue(b.text, TextRange(local.selection.start.coerceAtMost(b.text.length)))
    Box(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 8.dp)) {
        if (placeholder != null && b.text.isEmpty()) {
            Text(placeholder, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        BasicTextField(
            value = value,
            onValueChange = { v ->
                local = v
                if (v.text != b.text) cb.setText(b.key, v.text)
                cb.setCursor(b.key, v.selection.start)
            },
            textStyle = MaterialTheme.typography.bodyLarge.copy(color = MaterialTheme.colorScheme.onSurface),
            cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
            modifier = modifier.fillMaxWidth()
                .onFocusChanged { if (it.isFocused) cb.setCursor(b.key, value.selection.start) }
                .semantics { contentDescription = placeholder ?: messageText },
        )
    }
}

/** An inline image in the compose view: its size in the message (at most the screen's width),
 *  faded with a bar while it uploads, and a button that takes it out. */
@Composable
private fun InlineImage(b: BodyBlock.Image, bytes: ByteArray?, onRemove: () -> Unit) {
    val imageDesc = stringResource(if (b.uploading) R.string.compose_image_uploading else R.string.compose_image_in_message)
    // Decoded off the main thread: a large photo took long enough to drop frames while typing.
    val bmp = androidx.compose.runtime.produceState<androidx.compose.ui.graphics.ImageBitmap?>(null, bytes) {
        value = bytes?.let { b -> kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Default) { InlinePhotos.preview(b)?.asImageBitmap() } }
    }.value
    val ratio = when {
        b.width > 0 && b.height > 0 -> b.width.toFloat() / b.height
        bmp != null -> bmp.width.toFloat() / bmp.height
        else -> 4f / 3f
    }
    val width = (if (b.width > 0) b.width else bmp?.width?.coerceAtMost(InlinePhotos.INSERT_MAX_WIDTH) ?: 320).dp
    Box(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 6.dp)) {
        Box(
            Modifier.widthIn(max = width).fillMaxWidth().aspectRatio(ratio)
                .clip(RoundedCornerShape(8.dp))
                .background(MaterialTheme.colorScheme.surfaceContainerHighest)
                .semantics { contentDescription = imageDesc },
        ) {
            if (bmp != null) {
                Image(bmp, null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop, alpha = if (b.uploading) 0.55f else 1f)
            } else {
                Icon(Icons.Outlined.Image, null, Modifier.align(Alignment.Center).size(36.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (b.uploading) LinearProgressIndicator(Modifier.align(Alignment.BottomCenter).fillMaxWidth())
            FilledTonalIconButton(
                onClick = onRemove,
                modifier = Modifier.align(Alignment.TopEnd).padding(6.dp).size(32.dp),
                colors = IconButtonDefaults.filledTonalIconButtonColors(
                    containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.88f),
                    contentColor = MaterialTheme.colorScheme.onSurface),
            ) { Icon(Icons.Outlined.Close, stringResource(R.string.compose_remove_image), Modifier.size(18.dp)) }
        }
    }
}

private val IMG_TAG = Regex("""<img\b[^>]*>""", RegexOption.IGNORE_CASE)

/** HTML kept from the draft as written (formatting from the web, a table, a list). It is shown
 *  as rich text and sent unchanged; "Edit as plain text" drops its formatting to edit it. */
@Composable
private fun KeptHtml(b: BodyBlock.Html, onEdit: () -> Unit) {
    val imageMark = stringResource(R.string.compose_image_marker)
    val text = remember(b.html, imageMark) {
        // Display only: images as a marker, list items with a bullet, no trailing blank lines.
        val shown = IMG_TAG.replace(b.html) { imageMark }
            .replace(Regex("""(<li\b[^>]*>)\s*<p>""", RegexOption.IGNORE_CASE), "$1")
            .replace(Regex("""</p>\s*</li>""", RegexOption.IGNORE_CASE), "</li>")
            .replace(Regex("""<li\b[^>]*>""", RegexOption.IGNORE_CASE)) { it.value + "• " }
        val a = AnnotatedString.fromHtml(shown)
        a.subSequence(0, a.text.trimEnd().length)
    }
    Surface(
        shape = MaterialTheme.shapes.small,
        color = MaterialTheme.colorScheme.surfaceContainerLow,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
    ) {
        Column(Modifier.padding(start = 8.dp, top = 8.dp, end = 4.dp)) {
            Text(text, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.padding(horizontal = 4.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.compose_formatted_text), style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f).padding(start = 4.dp))
                TextButton(onClick = onEdit) { Text(stringResource(R.string.compose_edit_plain)) }
            }
        }
    }
}
