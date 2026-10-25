import { describe, expect, it } from 'vitest'
import { appLinkLabel, appPath } from './appLinks'

const O = 'https://mail.example.com'

describe('appLinks', () => {
  it('shows a link into this app short, by its area', () => {
    expect(appLinkLabel(`${O}/notes/n/n8c7hthcajzx#i-01a11323-426e`, O)).toBe('Open in Notes')
    expect(appLinkLabel(`${O}/tables/t/t3`, O)).toBe('Open in Tables')
    expect(appLinkLabel(`${O}/mail/inbox/c12`, O)).toBe('Open in Mail')
    expect(appPath(`${O}/notes/n/n8c7#i-1`, O)).toBe('/notes/n/n8c7#i-1')
  })
  it('leaves other links and the API alone', () => {
    expect(appLinkLabel('https://elsewhere.example/notes/n/n1', O)).toBeNull()
    expect(appLinkLabel(`${O}/api/v1/notes`, O)).toBeNull()
    expect(appLinkLabel(`${O}/settings`, O)).toBeNull()
    expect(appLinkLabel('not a url ::', '')).toBeNull()
  })
})
