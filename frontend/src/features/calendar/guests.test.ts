import { describe, expect, it } from 'vitest'
import { isOutside, orgDomains, outsideWarning } from './guests'

describe('guests outside your organisation', () => {
  it('takes my domains and leaves out free mail', () => {
    expect(orgDomains(['me@example.edu', 'me@gmail.com', 'x@Example.edu'])).toEqual(['example.edu'])
    expect(orgDomains(['me@gmail.com'])).toEqual([])
  })
  it('counts subdomains as inside and warns by name or count', () => {
    expect(isOutside('dana@cs.example.edu', ['example.edu'])).toBe(false)
    expect(isOutside('dana@example.org', ['example.edu'])).toBe(true)
    expect(isOutside('dana@example.org', [])).toBe(false)
    expect(outsideWarning([{ email: 'dana@example.org', name: 'Dana' }, { email: 'li@example.edu' }], ['example.edu'])).toBe('Dana is outside your organisation')
    expect(outsideWarning([{ email: 'a@x.org' }, { email: 'b@y.org' }], ['example.edu'])).toBe('2 guests are outside your organisation')
    expect(outsideWarning([{ email: 'li@example.edu' }], ['example.edu'])).toBeNull()
  })
})
