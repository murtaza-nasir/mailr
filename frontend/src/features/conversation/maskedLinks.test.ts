import { describe, expect, it, vi } from 'vitest'
import { captureMaskedLinks, maskedLink, shownHost } from './maskedLinks'

describe('masked links', () => {
  it('reads a host from link text that is an address or a domain', () => {
    expect(shownHost('www.yourbank.example')).toBe('yourbank.example')
    expect(shownHost(' https://YourBank.example/login?x=1 ')).toBe('yourbank.example')
    expect(shownHost('(portal.example.org).')).toBe('portal.example.org')
    expect(shownHost('Sign in to your account')).toBeNull()
    expect(shownHost('Read more')).toBeNull()
    expect(shownHost('v2.1')).toBeNull()
  })

  it('flags text naming one host on a link to another', () => {
    expect(maskedLink('www.yourbank.example', 'https://login.evil.example/x')).toEqual({ shown: 'yourbank.example', real: 'login.evil.example' })
    expect(maskedLink('yourbank.example', 'https://yourbank.example.evil.example/')).toEqual({ shown: 'yourbank.example', real: 'yourbank.example.evil.example' })
    // Unicode look-alikes compare in their ASCII form.
    expect(maskedLink('pаypal.example', 'https://paypal.example/')?.shown).toMatch(/^xn--/)
  })

  it('leaves links to the same site alone', () => {
    expect(maskedLink('https://www.yourbank.example', 'https://yourbank.example/login')).toBeNull()
    expect(maskedLink('yourbank.example', 'https://login.yourbank.example/')).toBeNull()
    expect(maskedLink('Click here', 'https://anywhere.example/')).toBeNull()
    expect(maskedLink('yourbank.example', 'mailto:help@other.example')).toBeNull()
  })

  it('asks before a click on a masked link goes anywhere', () => {
    // The tests run without a DOM: a document and links that answer what the handler asks.
    let handler: ((e: MouseEvent) => void) | null = null
    const doc = { addEventListener: (_: string, h: (e: MouseEvent) => void) => { handler = h }, removeEventListener: () => { handler = null } }
    const link = (href: string, text: string) => ({ nodeType: 1, closest: () => ({ getAttribute: () => href, textContent: text }) })
    const click = (target: unknown) => {
      const e = { defaultPrevented: false, button: 0, target, preventDefault: vi.fn(), stopPropagation: vi.fn() }
      handler!(e as unknown as MouseEvent)
      return e
    }
    const ask = vi.fn()
    const stop = captureMaskedLinks(doc as unknown as Document, ask)
    const masked = click(link('https://evil.example/', 'www.yourbank.example'))
    expect(masked.preventDefault).toHaveBeenCalled()
    expect(ask).toHaveBeenCalledWith({ href: 'https://evil.example/', shown: 'yourbank.example', real: 'evil.example' })
    const plain = click(link('https://news.example/', 'Read the news'))
    expect(plain.preventDefault).not.toHaveBeenCalled()
    expect(ask).toHaveBeenCalledTimes(1)
    stop()
    expect(handler).toBeNull()
  })
})
