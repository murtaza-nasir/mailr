import { describe, expect, it } from 'vitest'
import { editorSafeSrc, imageSources } from './mailImage'

const el = (attrs: Record<string, string>) => ({ getAttribute: (n: string) => attrs[n] ?? null }) as unknown as Element

describe('compose editor image sources', () => {
  it('loads only data:, blob:, cid: and Mailr paths', () => {
    expect(editorSafeSrc('/api/v1/uploads/' + 'a'.repeat(32))).toBe(true)
    expect(editorSafeSrc('/api/v1/messages/m1/images/' + 'a'.repeat(32))).toBe(true)
    expect(editorSafeSrc('data:image/png;base64,AAAA')).toBe(true)
    expect(editorSafeSrc('blob:http://localhost/x')).toBe(true)
    expect(editorSafeSrc('https://evil.test/leak.png')).toBe(false)
    expect(editorSafeSrc('//evil.test/leak.png')).toBe(false)
    expect(editorSafeSrc('/\\evil.test/leak.png')).toBe(false)
    expect(editorSafeSrc('')).toBe(false)
  })

  it('keeps a remote address aside instead of loading it', () => {
    expect(imageSources(el({ src: 'https://evil.test/a.png' }))).toEqual({ src: null, mailrSrc: 'https://evil.test/a.png' })
    expect(imageSources(el({ src: '', 'data-mailr-src': 'https://evil.test/a.png' }))).toEqual({ src: null, mailrSrc: 'https://evil.test/a.png' })
    const proxied = '/api/v1/messages/m1/images/' + 'b'.repeat(32)
    expect(imageSources(el({ src: proxied, 'data-mailr-src': 'https://evil.test/a.png' }))).toEqual({ src: proxied, mailrSrc: 'https://evil.test/a.png' })
    expect(imageSources(el({ src: 'javascript:alert(1)', 'data-mailr-src': 'javascript:x' }))).toEqual({ src: null, mailrSrc: null })
  })
})
