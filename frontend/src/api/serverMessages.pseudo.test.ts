// en-XA, the pseudo-language: a coded server error shows as pseudo text, so an error that still has no
// code stands out in plain English.
import { describe, expect, it, vi } from 'vitest'

vi.mock('../i18n', async (importOriginal) => ({ ...(await importOriginal<typeof import('../i18n')>()), language: () => 'en-XA' }))

describe('server errors in en-XA', () => {
  it('turns a coded detail into pseudo text and leaves an uncoded one', async () => {
    const { codedText, serverText } = await import('./serverMessages')
    const { pseudo } = await import('../i18n')
    expect(serverText('templates.template_not_found', {}, 'template not found')).toBe(pseudo('template not found'))
    expect(await codedText({ detail: 'template not found', code: 'templates.template_not_found' })).toBe('[ţéɱƥļáţé ñóţ ƒóúñð ••••••]')
    expect(await codedText({ detail: 'spec must be JSON' })).toBeNull()
  })
})
