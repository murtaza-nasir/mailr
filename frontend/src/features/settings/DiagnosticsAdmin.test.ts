import { describe, expect, it } from 'vitest'
import { checkTone, orderChecks, outcomeText, queueText, waitText } from './DiagnosticsAdmin'
import { healthSummary } from './AccountsTab'
import type { Connector } from '../../api/types'

describe('Admin → Diagnostics', () => {
  it('lists failing checks first, then the information-only ones, then the rest', () => {
    const c = (name: string, ok: boolean, required = true) => ({ name, label: name, ok, detail: '', required })
    expect(orderChecks([c('db', true), c('push', false, false), c('backup_target', false), c('store', true)]).map((x) => x.name))
      .toEqual(['backup_target', 'push', 'db', 'store'])
  })

  it('shows a check that is turned off as information, never as failing or passing', () => {
    const c = (ok: boolean, required: boolean, off?: boolean) => ({ name: 'export', label: 'export', ok, detail: '', required, off })
    expect(checkTone(c(true, true, true))).toBe('info')
    expect(checkTone(c(true, true))).toBe('ok')
    expect(checkTone(c(false, true))).toBe('bad')
    expect(checkTone(c(false, false))).toBe('info')
  })

  it('says how long the oldest job waits', () => {
    expect(waitText(null)).toBe('nothing waiting')
    expect(waitText(42)).toBe('42 s')
    expect(waitText(600)).toBe('10 min')
    expect(waitText(7200)).toBe('2 h')
    expect(queueText({ queued: 0, running: 0, due: 0, oldest_due_seconds: null, failed_last_day: 0 })).toBe('No jobs due now · 0 running · none scheduled for later')
    expect(queueText({ queued: 5, running: 1, due: 2, oldest_due_seconds: 120, failed_last_day: 0 })).toBe('2 jobs due now, the oldest waiting 2 min · 1 running · 3 scheduled for later')
  })

  it('reads a last success and a last error', () => {
    expect(outcomeText(null)).toEqual({ tone: 'none', text: 'Not used yet' })
    expect(outcomeText({ ok: false, error: 'The server refused the API key.', error_at: new Date().toISOString() }).tone).toBe('err')
    expect(outcomeText({ ok: true, ok_at: new Date().toISOString() }).text).toMatch(/^Worked /)
  })
})

describe('Settings → Accounts health line', () => {
  const acct = (name: string, state: NonNullable<Connector['health']>['state'], text: string) =>
    ({ name, health: { state, text }, last_sync_at: null }) as unknown as Connector
  it('names the account that needs its password first', () => {
    expect(healthSummary([acct('Home', 'ok', 'Up to date'), acct('Work', 'signin', 'Needs its password again')]))
      .toEqual({ state: 'signin', text: 'Work: Needs its password again.' })
    expect(healthSummary([])).toBeNull()
  })
  it('shows a sync only while one is running or behind, as the rows do', () => {
    // Every row says "Up to date": no line ("Your account is syncing" above "Up to date" disagreed).
    expect(healthSummary([acct('Home', 'ok', 'Up to date')])).toBeNull()
    expect(healthSummary([acct('Home', 'ok', 'Up to date'), acct('Club', 'ok', 'Up to date')])).toBeNull()
    expect(healthSummary([acct('Home', 'syncing', 'First sync in progress')]))
      .toEqual({ state: 'syncing', text: 'Your account is syncing.' })
    expect(healthSummary([acct('Home', 'syncing', 'Syncing'), acct('Club', 'syncing', 'Syncing')])?.text).toBe('All 2 accounts are syncing.')
    expect(healthSummary([acct('Home', 'ok', 'Up to date'), acct('Club', 'syncing', 'First sync in progress')]))
      .toEqual({ state: 'syncing', text: 'Club: First sync in progress.' })
  })
})
