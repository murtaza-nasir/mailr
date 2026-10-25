import { describe, expect, it } from 'vitest'
import { phoneView } from './phoneView'

describe('the calendar view on a phone', () => {
  it('Week shows three days on a narrow screen, as the 3-day view', () => {
    expect(phoneView('week', true)).toBe('ndays')
    expect(phoneView('week', false)).toBe('week')
    expect(phoneView('month', true)).toBe('month')
  })
})
