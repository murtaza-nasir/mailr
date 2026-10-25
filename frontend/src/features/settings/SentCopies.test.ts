// ACT-SURFACES: web.settings
import { describe, expect, it } from 'vitest'
import type { SentCopies } from '../../api/types'
import { SAVE_SENT_CHOICES, backfillBody, backfillSummary, saveSentHint, saveSentLabel, sentCopiesWarning, type SentBackfillJob } from './SentCopies'

const fastmail: SentCopies = { setting: 'auto', active: true, provider_saves: false, provider: 'Fastmail', failed: 0 }
const gmail: SentCopies = { setting: 'auto', active: false, provider_saves: true, provider: 'Gmail', failed: 0 }

function job(over: Partial<SentBackfillJob>): SentBackfillJob {
  return { job: 7, status: 'done', dry_run: true, since: null, done: 3, total: 3, result: { total: 3, present: 1, copied: 2, failed: 0 }, error: '', ...over }
}

describe('Save sent mail at the provider (settings.account_save_sent)', () => {
  it('offers Automatic, On and Off', () => {
    expect(SAVE_SENT_CHOICES).toEqual(['auto', 'on', 'off'])
    expect(SAVE_SENT_CHOICES.map(saveSentLabel)).toEqual(['Automatic', 'On', 'Off'])
  })

  it('says what the choice does for this provider', () => {
    expect(saveSentHint(fastmail, 'auto')).toBe("A copy of each message sent from Mailr is put in Fastmail's Sent folder.")
    expect(saveSentHint(gmail, 'auto')).toMatch(/^Off for this account: Gmail puts the mail sent through it/)
    expect(saveSentHint(gmail, 'on')).toMatch(/Gmail saves sent mail itself\. A copy is added only when/)
    expect(saveSentHint(fastmail, 'off')).toBe("Mail sent from Mailr stays in Mailr's Sent only. Fastmail's Sent folder gets no copy.")
  })
})

describe('Copy past sent mail (settings.account_sent_backfill)', () => {
  it('counts first, then copies, from a date or everything', () => {
    expect(backfillBody(true, '')).toEqual({ dry_run: true, since: null })
    expect(backfillBody(false, '2026-01-01')).toEqual({ dry_run: false, since: '2026-01-01' })
  })

  it('reports the count and the copy', () => {
    expect(backfillSummary(job({}), 'Fastmail')).toBe("2 of 3 sent messages are missing in Fastmail's Sent folder.")
    expect(backfillSummary(job({ result: { total: 2, present: 2, copied: 0, failed: 0 } }), 'Fastmail')).toBe('Nothing to copy. 2 messages checked.')
    expect(backfillSummary(job({ dry_run: false }), 'Fastmail')).toBe("Copied 2 messages to Fastmail's Sent folder. 1 was there already.")
    expect(backfillSummary(job({ dry_run: false, result: { total: 3, present: 0, copied: 2, failed: 1 } }), 'Fastmail')).toMatch(/1 could not be copied\. Try again later\.$/)
    expect(backfillSummary(job({ status: 'failed', result: null, error: 'only an IMAP account set to “Keep in sync” saves sent mail at the provider' }), 'Fastmail')).toMatch(/Keep in sync/)
  })
})

describe('Sent messages that could not be saved (settings.account_sent_retry)', () => {
  it('shows the warning with its Retry only while there are some', () => {
    expect(sentCopiesWarning(fastmail)).toBeNull()
    expect(sentCopiesWarning(null)).toBeNull()
    expect(sentCopiesWarning({ ...fastmail, failed: 1 })).toBe("1 sent message could not be saved in Fastmail's Sent folder")
    expect(sentCopiesWarning({ ...fastmail, failed: 4 })).toBe("4 sent messages could not be saved in Fastmail's Sent folder")
  })
})
