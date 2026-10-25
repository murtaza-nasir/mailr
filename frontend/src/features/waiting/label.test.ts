import { describe, expect, it } from 'vitest'
import { waitingLabel } from './label'

describe('waitingLabel', () => {
  it('names what is waiting', () => {
    expect(waitingLabel({ approvals: 0, notSent: 0, reminders: 0, missed: 0 })).toBe('Nothing waiting')
    expect(waitingLabel({ approvals: 1, notSent: 0, reminders: 0, missed: 0 })).toBe('Waiting for you: 1 approval')
    expect(waitingLabel({ approvals: 2, notSent: 1, reminders: 3, missed: 1 })).toBe('Waiting for you: 2 approvals, 1 not sent, 3 reminders (1 missed)')
    expect(waitingLabel({ approvals: 0, notSent: 0, reminders: 2, missed: 2 })).toBe('Waiting for you: 2 reminders (2 missed)')
  })
})
