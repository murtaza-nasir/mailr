// The assistant's edits of an open compose window: the server's HTML (its Markdown made into
// formatting) goes into the editor; an older server's plain text is escaped as before.
import { describe, expect, it } from 'vitest'
import { applyEditsToHtml, asEditorHtml, editBodyHtml } from './composeText'

describe('assistant edits in the editor', () => {
  it('uses the HTML the server sent for a new body', () => {
    const html = '<div>Thanks.</div><ul><li><strong>Theo:</strong> Share the outlines.</li></ul>'
    expect(editBodyHtml({ body_text: 'Thanks.\n* **Theo:** Share the outlines.', body_html: html })).toBe(html)
  })

  it('escapes plain text from an older server', () => {
    expect(editBodyHtml({ body_text: 'a < b\n\n**x**' })).toBe('<p>a &lt; b</p><p>**x**</p>')
  })

  it('puts a replacement in as HTML when the server sent one', () => {
    const edits = [{ find: 'next steps', replace: '**next steps**', replace_html: '<strong>next steps</strong>' }]
    expect(applyEditsToHtml('<p>The next steps follow.</p>', edits)).toBe('<p>The <strong>next steps</strong> follow.</p>')
    expect(applyEditsToHtml('<p>The next steps follow.</p>', [{ find: 'next steps', replace: 'a & b' }]))
      .toBe('<p>The a &amp; b follow.</p>')
    expect(applyEditsToHtml('<p>Nothing here.</p>', edits)).toBeNull()
  })
})

describe('asEditorHtml', () => {
  it('turns a block holding only a line break into an empty paragraph (one blank line)', () => {
    expect(asEditorHtml('<div>Hi,</div><div><br></div><div>Here are the items:</div><ul><li>a</li></ul>'))
      .toBe('<div>Hi,</div><p></p><div>Here are the items:</div><ul><li>a</li></ul>')
    expect(asEditorHtml('<p dir="auto"><br/></p>')).toBe('<p dir="auto"></p>')
  })
  it('keeps line breaks that sit beside text', () => {
    const html = '<div>Love,<br>Rosa</div><div>a<br></div>'
    expect(asEditorHtml(html)).toBe(html)
  })
})

describe('editBodyHtml', () => {
  it('normalises the server HTML too', () => {
    expect(editBodyHtml({ body_html: '<div>Hi,</div><div><br></div><div>Thanks.</div>' })).toBe('<div>Hi,</div><p></p><div>Thanks.</div>')
  })
})
