import { describe, expect, it } from 'vitest'
import { modeName, providerName } from './syncModes'

describe('sync modes', () => {
  it('has one name per mode', () => {
    expect(modeName('mirror')).toBe('Keep in sync')
    expect(modeName('store')).toBe('Mailr keeps the mail')
    expect(modeName('import')).toBe('Copy once')
  })
  it('names the provider by its preset, its server or the address', () => {
    expect(providerName({ kind: 'imap', preset: 'fastmail', config: {} })).toBe('Fastmail')
    expect(providerName({ kind: 'gmail', preset: null, config: {} })).toBe('Gmail')
    expect(providerName({ kind: 'imap', preset: null, config: { imap: { host: 'imap.example.org' } } })).toBe('example.org')
    expect(providerName({ kind: 'imap', preset: null, config: { imap: { host: 'localhost' }, email: 'ana@northwind.example' } })).toBe('northwind.example')
    expect(providerName({ kind: 'imap', preset: null, config: { imap: { host: '127.0.0.1' } } })).toBe('the provider')
  })
})
