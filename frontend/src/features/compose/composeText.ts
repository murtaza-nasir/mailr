/** Text helpers of the compose form: the assistant's edits and plain text become editor HTML. */

/** Escape text the way the editor serialises text nodes (only &, < and >). */
function escText(s: string): string {
  return s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]!)
}

/** HTML for the editor: a blank line written as a block with only a line break (`<div><br></div>`,
 *  as email HTML and the server's Markdown conversion write it) becomes an empty paragraph. The
 *  editor would keep the break inside the paragraph and show two blank lines (after "Hi," in a
 *  Help me write draft); an empty paragraph is one blank line, as when you press Enter twice. */
export function asEditorHtml(html: string): string {
  return html.replace(/<(p|div)((?:\s[^>]*)?)>\s*<br\s*\/?>\s*<\/\1>/gi, '<p$2></p>')
}

/** Plain text (blank lines between paragraphs) → editor HTML. */
export function textToParagraphs(text: string): string {
  return text
    .replace(/\r\n/g, '\n')
    .split(/\n{2,}/)
    .map((p) => `<p>${escText(p).replace(/\n/g, '<br>')}</p>`)
    .join('')
}

/** Apply find/replace pairs to editor HTML when every find text is present; null otherwise.
 *  A replacement the server sent as HTML (replace_html: the model's **bold** as formatting) goes in as it is. */
export function applyEditsToHtml(html: string, edits: { find: string; replace: string; replace_html?: string }[]): string | null {
  let out = html
  for (const e of edits) {
    const f = escText(e.find)
    if (!f || !out.includes(f)) return null
    out = out.split(f).join(e.replace_html ?? escText(e.replace).replace(/\n/g, '<br>'))
  }
  return out
}

/** The new body of an assistant edit as editor HTML: the server's HTML when it sent one, else the text. */
export function editBodyHtml(edit: { body_text?: string; body_html?: string }): string {
  return edit.body_html != null ? asEditorHtml(edit.body_html) : textToParagraphs(edit.body_text ?? '')
}
