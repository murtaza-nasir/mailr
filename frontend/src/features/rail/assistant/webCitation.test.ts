// ACT-SURFACES: web.panel
// agent.web_search: a web source the assistant cites ([[web:<url>]]) is a chip with a globe, its site
// and title; it opens the page in a new tab outside mailr, never an in-app route.
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { citationRoute, openWebCitation, webUrl, type Citation } from './citations'
import { Markdown } from './Markdown'

const web = (extra: Partial<Citation> = {}): Citation => ({
  n: 1, kind: 'web', conversation_id: null, message_id: null, subject: 'The Linux Kernel Archives', from: 'kernel.org',
  date: '2026-10-03', quote: null, verified: true, start: null, end: null, url: 'https://www.kernel.org/',
  title: 'The Linux Kernel Archives', site: 'kernel.org', provider: 'Exa', ...extra,
})

afterEach(() => vi.unstubAllGlobals())

describe('agent.web_search citations', () => {
  it('render as a web chip with the title and site', () => {
    const html = renderToStaticMarkup(createElement(Markdown, { text: 'Stable is 7.2.9 [1](mailr-cite:1).', citations: [web()], width: 400 }))
    expect(html).toContain('cite-chip web')
    expect(html).toContain('Web source 1: The Linux Kernel Archives, kernel.org. Opens in a new tab')
  })

  it('open the page outside Mailr, with no opener or referrer', () => {
    const open = vi.fn()
    vi.stubGlobal('window', { open })
    expect(openWebCitation(web())).toBe(true)
    expect(open).toHaveBeenCalledWith('https://www.kernel.org/', '_blank', 'noopener,noreferrer')
  })

  it('never open anything but an http(s) page, and have no in-app route', () => {
    const open = vi.fn()
    vi.stubGlobal('window', { open })
    expect(webUrl(web({ url: 'javascript:alert(1)' }))).toBeNull()
    expect(openWebCitation(web({ url: 'javascript:alert(1)' }))).toBe(false)
    expect(openWebCitation({ kind: 'message', url: 'https://www.kernel.org/' })).toBe(false)
    expect(open).not.toHaveBeenCalled()
    expect(citationRoute(web())).toBeNull()
  })
})
