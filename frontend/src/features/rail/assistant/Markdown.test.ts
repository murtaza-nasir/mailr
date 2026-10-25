import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { hostOf, Markdown } from './Markdown'

describe('assistant Markdown images', () => {
  it('an invalid image URL with no alt text renders a label instead of throwing', () => {
    expect(hostOf('http://[')).toBe('image')
    const html = renderToStaticMarkup(createElement(Markdown, { text: 'before ![](http://[) after', width: 400 }))
    expect(html).toContain('before')
    expect(html).not.toContain('<img')
  })

  it('never renders an <img> from model output', () => {
    const html = renderToStaticMarkup(createElement(Markdown, { text: '![x](https://evil.test/p.png)', width: 400 }))
    expect(html).not.toContain('<img')
    expect(html).toContain('[image: x]')
  })
})
