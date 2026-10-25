import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { rehypeDirAuto } from './rehypeDirAuto'

const html = (md: string) => renderToStaticMarkup(createElement(ReactMarkdown, { remarkPlugins: [remarkGfm], rehypePlugins: [rehypeDirAuto] }, md))

describe('rehypeDirAuto', () => {
  it('gives every block its own direction', () => {
    const out = html('## خلاصه\n\nשלום, עולם!\n\n- أول\n- Dan will send it.\n\n> نقل\n')
    for (const tag of ['h2', 'p', 'ul', 'li', 'blockquote']) expect(out).toContain(`<${tag} dir="auto">`)
  })
  it('leaves code and tables in shape', () => {
    const out = html('```\nx = 1\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n')
    expect(out).not.toMatch(/<(pre|code) dir=/)
    expect(out).toContain('<td dir="auto">1</td>')
  })
})
