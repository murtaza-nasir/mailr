import { describe, expect, it } from 'vitest'
import type { Identity } from '../api/types'
import { localPictureVersion, ownPictureUrl, setLocalPictures, sha256Hex } from './avatars'

const ident = (over: Partial<Identity>): Identity =>
  ({ id: 1, email: 'me@example.com', name: '', reply_to: '', signature_html: '', is_default: false, connector_id: null, receives: [], ...over }) as Identity

describe('ownPictureUrl: identity, then account, then initials', () => {
  const account = { has: true, source: 'upload' as const, version: 42, idp_available: false }
  it('prefers the identity picture', () => {
    const me = { identities: [ident({ id: 7, is_default: true, has_avatar: true })], picture: account }
    expect(ownPictureUrl(me)).toBe('/api/v1/identities/7/avatar')
  })
  it('falls back to the account picture', () => {
    const me = { identities: [ident({ id: 7, is_default: true, has_avatar: false })], picture: account }
    expect(ownPictureUrl(me)).toBe('/api/v1/me/picture?v=42')
    expect(ownPictureUrl(me, 'ME@example.com')).toBe('/api/v1/me/picture?v=42')
  })
  it('is null without pictures (initials) and for addresses that are not yours', () => {
    const me = { identities: [ident({ id: 7, is_default: true })], picture: { ...account, has: false } }
    expect(ownPictureUrl(me)).toBeNull()
    expect(ownPictureUrl({ identities: me.identities, picture: account }, 'stranger@example.org')).toBeNull()
    expect(ownPictureUrl(undefined)).toBeNull()
  })
  it('uses the default identity when no address is given', () => {
    const me = { identities: [ident({ id: 1, has_avatar: true }), ident({ id: 2, email: 'b@example.com', is_default: true })], picture: account }
    expect(ownPictureUrl(me)).toBe('/api/v1/me/picture?v=42')
  })
})

describe('pictures of this server users', () => {
  it('hashes like the server (sha256 of the lower-case address)', async () => {
    expect(await sha256Hex('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
  })
  it('knows an address once its hash is computed', async () => {
    setLocalPictures({ [await sha256Hex('fam@example.org')]: 5 })
    expect(localPictureVersion('Fam@Example.org ')).toBeNull() // hashing starts
    await new Promise((r) => setTimeout(r, 20))
    expect(localPictureVersion('fam@example.org')).toBe(5)
    expect(localPictureVersion('stranger@example.org')).toBeNull()
  })
})
