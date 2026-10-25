// The Speakr webhook in the connector card (speakr.connect; backend integrations/speakr/webhook.py):
// the answers of POST/DELETE /speakr/connectors/{id}/webhook and of .../webhook/test, and the
// messages the card shows for them.

import { t } from '../../i18n'

export interface WebhookOn {
  on: boolean
  managed: boolean
  path: string | null
  url: string | null
  speakr_webhook_id: number | null
  events: string[]
}

export interface WebhookOff extends WebhookOn {
  deleted_in_speakr: boolean | null
}

export interface WebhookTest {
  received: boolean
  speakr_status: string | null
  response_status: number | null
  error: string | null
  hint?: string | null
}

/** An older Speakr sends recording.updated only for edits made through its API, so the checks stay on. */
export const POLL_NOTE = t('settings.speakrWebhook.editsMadeInSpeakrsOwn')
/** A newer Speakr (signed send time, V2) reports edits from its own pages too. */
export const POLL_NOTE_V2 = t('settings.speakrWebhook.speakrReportsEditsMadeIn')

export function pollNote(v2: boolean | undefined): string {
  return v2 ? POLL_NOTE_V2 : POLL_NOTE
}

/** The token's access as the card shows it: null for an older Speakr, whose tokens have no scopes. */
export function scopeLabel(scopes: string[] | null | undefined): string | null {
  if (!scopes) return null
  if (scopes.includes('full')) return t('settings.speakrWebhook.tokenAccessFull')
  return t('settings.speakrWebhook.tokenAccess', { scopes: scopes.join(', ') || 'none' })
}

// ACT speakr.connect @web.settings
export function webhookTestMessage(out: WebhookTest): string {
  if (out.received) return t('settings.speakrWebhook.speakrReachedMailrTheTest')
  const why = out.hint || out.error || (out.response_status ? t('settings.speakrWebhook.mailrAnswered', { responseStatus: out.response_status }) : '')
  return t('settings.speakrWebhook.theTestDidNotArrive', { value: out.speakr_status ? t('settings.speakrWebhook.speakr', { speakrStatus: out.speakr_status }) : '', value2: why ? ` ${why}` : '' })
}

export function webhookOffMessage(out: WebhookOff): string {
  if (out.deleted_in_speakr === false) {
    return t('settings.speakrWebhook.webhookOffInMailrSpeakr')
  }
  return t('settings.speakrWebhook.webhookOffSpeakrIsChecked')
}
