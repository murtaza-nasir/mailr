// ACT-SURFACES: web.settings
import { describe, expect, it } from 'vitest'
import { bimiDomains, pictureSourceText, publishGuidance, type ProviderItem } from './PictureSection'

describe('Your picture', () => {
  it('says where the picture came from', () => {
    expect(pictureSourceText(undefined)).toMatch(/initials/)
    expect(pictureSourceText({ has: true, source: 'idp', version: 1, idp_available: true })).toMatch(/sign-in provider/)
    expect(pictureSourceText({ has: true, source: 'upload', version: 1, idp_available: false })).toMatch(/Uploaded/)
  })
  const items: ProviderItem[] = [
    { identity_id: 1, email: 'a@gmail.com', provider: 'google', domain: 'gmail.com', shared_domain: true },
    { identity_id: 2, email: 'me@brand.example', provider: null, domain: 'brand.example', shared_domain: false },
    { identity_id: 3, email: 'you@brand.example', provider: null, domain: 'brand.example', shared_domain: false },
    { identity_id: 4, email: 'b@outlook.com', provider: 'microsoft', domain: 'outlook.com', shared_domain: true },
    { identity_id: 5, email: 'c@yahoo.com', provider: null, domain: 'yahoo.com', shared_domain: true },
  ]
  it('offers one BIMI check per own domain, never for a shared mailbox domain (settings.picture_bimi_check)', () => {
    expect(bimiDomains(items)).toEqual([{ domain: 'brand.example', identity_id: 2 }])
  })
  it('matches the guidance to the sending addresses (settings.picture_publish_check)', () => {
    const g = publishGuidance(items, false)
    expect(g.google.map((i) => i.email)).toEqual(['a@gmail.com'])
    expect(g.microsoft.map((i) => i.email)).toEqual(['b@outlook.com'])
    expect(g.other.map((i) => i.email)).toEqual(['me@brand.example', 'you@brand.example', 'c@yahoo.com'])
    expect(g.bimi).toEqual([])
    expect(publishGuidance(items, true).bimi).toEqual([{ domain: 'brand.example', identity_id: 2 }])
  })
  it('shows only the Google part to a Gmail-only user', () => {
    const g = publishGuidance([items[0]], true)
    expect(g.google).toHaveLength(1)
    expect(g.microsoft).toHaveLength(0)
    expect(g.other).toHaveLength(0)
    expect(g.bimi).toHaveLength(0)
  })
})
