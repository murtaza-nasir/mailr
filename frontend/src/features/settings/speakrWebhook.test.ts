// speakr.connect: the messages for the webhook's Send test and Turn off.
import { describe, expect, it } from 'vitest'
import { POLL_NOTE, pollNote, scopeLabel, webhookOffMessage, webhookTestMessage } from './speakrWebhook'

const base = { on: false, managed: false, path: null, url: null, speakr_webhook_id: null, events: [] }

describe('speakr webhook messages', () => {
  it('says when the test arrived', () => {
    expect(webhookTestMessage({ received: true, speakr_status: 'success', response_status: 200, error: null })).toBe('Speakr reached Mailr: the test arrived.')
  })
  it('passes on the hint about a private address or a proxy sign-in', () => {
    const msg = webhookTestMessage({ received: false, speakr_status: 'failed', response_status: null, error: 'blocked at delivery', hint: 'On the Speakr server, set WEBHOOK_INTRANET_HOST_ALLOWLIST=^mail\\.example\\.org$' })
    expect(msg).toContain('(Speakr: failed)')
    expect(msg).toContain('WEBHOOK_INTRANET_HOST_ALLOWLIST')
    expect(webhookTestMessage({ received: false, speakr_status: 'permanent_failure', response_status: 401, error: null })).toContain('Mailr response: 401')
  })
  it('asks for a manual delete when Speakr could not be reached', () => {
    expect(webhookOffMessage({ ...base, deleted_in_speakr: false })).toContain('Account Settings → Webhooks')
    expect(webhookOffMessage({ ...base, deleted_in_speakr: true })).toBe('Webhook off. Speakr is checked every ten minutes.')
    expect(POLL_NOTE).toContain('ten-minute check')
  })
})

// ACT speakr.connect
describe('Speakr token scopes and update notes', () => {
  it('names the token access and the note for each Speakr', () => {
    expect(scopeLabel(null)).toBeNull()
    expect(scopeLabel(['full'])).toBe('Token access: full')
    expect(scopeLabel(['read', 'write'])).toBe('Token access: read, write')
    expect(pollNote(false)).toBe(POLL_NOTE)
    expect(pollNote(true)).toContain('its own pages too')
  })
})
