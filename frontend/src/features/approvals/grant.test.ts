// ACT-SURFACES: web.reader
// approval.undo_grant: the text a grant button and its toasts show (grant.ts).
import { describe, expect, it } from 'vitest'
import { alsoLabel, grantToast, scopeHelp, undoToast } from './grant'

describe('grant texts', () => {
  it('names the other waiting requests on the button, only when there are some', () => {
    expect(alsoLabel(0)).toBe('')
    expect(alsoLabel(undefined)).toBe('')
    expect(alsoLabel(2)).toBe(' (also approves 2 other waiting)')
  })

  it('says what the grant did', () => {
    expect(grantToast('session', 'Allow for 12 hours', 2)).toBe('Allowed for 12 hours. Also approved 2 other requests.')
    expect(grantToast('session', 'Allow for this chat', 1)).toBe('Allowed for this chat. Also approved 1 other request.')
    expect(grantToast('always', 'Allow for 12 hours', 3)).toBe('Rule created. Also approved 3 other requests.')
  })

  it('says honestly what Undo could not take back', () => {
    expect(undoToast(2, 0)).toBe('Undone. 2 requests are waiting again.')
    expect(undoToast(1, 0)).toBe('Undone. 1 request is waiting again.')
    expect(undoToast(1, 1)).toBe('Undone for 1 request. 1 request had already run.')
    expect(undoToast(0, 2)).toBe('The rule is removed, but 2 requests had already run.')
  })

  it('explains what each choice covers', () => {
    const h = scopeHelp('create or change labels', 'Allow for 12 hours')
    expect(h.once).toBe('This request only.')
    expect(h.session).toMatch(/^This agent may create or change labels for 12 hours without asking, with any input\./)
    expect(h.always).toMatch(/^A standing rule/)
  })
})
